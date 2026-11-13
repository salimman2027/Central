package sa.nq.phone

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.CallLog
import android.provider.MediaStore
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/**
 * بيدوّر على تسجيلات مكالمات الشريحة اللي مسجّل الموبايل نفسه عملها (سامسونج وغيره)،
 * ويطابق كل تسجيل بالمكالمة من سجل المكالمات (الرقم والوقت والمدة)، ويرفعه على السيرفر.
 */
object SimRecSync {
    private const val PREFS = "nq_simrec"
    private val running = AtomicBoolean(false)

    fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun configure(ctx: Context, host: String, ext: String, pass: String, enabled: Boolean) {
        prefs(ctx).edit().putString("host", host).putString("ext", ext).putString("pass", pass)
            .putBoolean("enabled", enabled).apply()
    }

    fun neededPermissions(): Array<String> {
        val audio = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_AUDIO
        else Manifest.permission.READ_EXTERNAL_STORAGE
        return arrayOf(Manifest.permission.READ_CALL_LOG, audio)
    }

    fun hasPermissions(ctx: Context) = neededPermissions().all {
        ctx.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
    }

    fun statusJson(ctx: Context): String {
        val p = prefs(ctx)
        return org.json.JSONObject()
            .put("enabled", p.getBoolean("enabled", false))
            .put("perms", hasPermissions(ctx))
            .put("count", p.getInt("count", 0))
            .put("lastUpload", p.getLong("lastUpload", 0))
            .put("lastRun", p.getLong("lastRun", 0))
            .put("error", p.getString("error", "") ?: "")
            .toString()
    }

    /** بيشتغل في thread لوحده؛ لو في مزامنة شغالة بيرجع على طول */
    fun runAsync(ctx: Context, onDone: (() -> Unit)? = null) {
        val app = ctx.applicationContext
        if (!running.compareAndSet(false, true)) return
        Thread {
            try { run(app) } catch (e: Exception) {
                prefs(app).edit().putString("error", e.message ?: e.toString()).apply()
            } finally { running.set(false); onDone?.invoke() }
        }.start()
    }

    private data class CallRow(val number: String, val start: Long, val durSec: Long, val incoming: Boolean)
    private data class Rec(val id: Long, val uri: Uri, val name: String, val path: String, val added: Long, val durMs: Long)

    private fun run(ctx: Context) {
        val p = prefs(ctx)
        if (!p.getBoolean("enabled", false) || !hasPermissions(ctx)) return
        val host = p.getString("host", "") ?: ""
        val ext = p.getString("ext", "") ?: ""
        val pass = p.getString("pass", "") ?: ""
        if (host.isBlank() || ext.isBlank() || pass.isBlank()) {
            p.edit().putString("error", "حط رقم التحويلة والباسورد في الإعدادات").apply(); return
        }
        val now = System.currentTimeMillis()
        // أول مرة: آخر 3 أيام بس
        val since = p.getLong("since", now - 3L * 24 * 3600 * 1000)
        val done = (p.getStringSet("done", emptySet()) ?: emptySet()).toMutableSet()
        val usedMedia = (p.getStringSet("media", emptySet()) ?: emptySet()).toMutableSet()

        val calls = mutableListOf<CallRow>()
        ctx.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.DATE, CallLog.Calls.DURATION, CallLog.Calls.TYPE),
            "${CallLog.Calls.DATE} > ? AND ${CallLog.Calls.DURATION} > 0", arrayOf(since.toString()),
            "${CallLog.Calls.DATE} ASC"
        )?.use { c ->
            while (c.moveToNext()) {
                val type = c.getInt(3)
                if (type != CallLog.Calls.INCOMING_TYPE && type != CallLog.Calls.OUTGOING_TYPE) continue
                calls += CallRow(c.getString(0) ?: "", c.getLong(1), c.getLong(2), type == CallLog.Calls.INCOMING_TYPE)
            }
        }

        var newSince = since
        var error = ""
        for (call in calls) {
            val key = call.start.toString()
            if (key in done) { newSince = maxOf(newSince, call.start); continue }
            val end = call.start + call.durSec * 1000
            if (now < end + 20_000) break // المسجّل لسه بيكتب الملف
            val rec = findRecording(ctx, call, end, usedMedia)
            if (rec == null) {
                // بعد 20 دقيقة من غير تسجيل → المكالمة دي مكانش ليها تسجيل
                if (now > end + 20 * 60_000) { done += key; newSince = maxOf(newSince, call.start) }
                continue
            }
            val code = upload(ctx, host, ext, pass, call, rec)
            when (code) {
                200 -> {
                    done += key; usedMedia += rec.id.toString(); newSince = maxOf(newSince, call.start)
                    p.edit().putInt("count", p.getInt("count", 0) + 1).putLong("lastUpload", now).apply()
                }
                401 -> { error = "رقم التحويلة أو الباسورد غلط"; break }
                413, 400 -> { done += key; usedMedia += rec.id.toString() } // ملف مرفوض — مانحاولش تاني
                else -> { error = "تعذّر الرفع (كود $code) — هيحاول تاني"; break }
            }
        }
        // نخلي القوايم صغيرة
        val keep = done.sortedBy { it.toLongOrNull() ?: 0 }.takeLast(500).toSet()
        p.edit().putLong("since", maxOf(since, newSince - 60 * 60_000))
            .putStringSet("done", keep).putStringSet("media", usedMedia.toList().takeLast(500).toSet())
            .putLong("lastRun", now).putString("error", error).apply()
    }

    private fun digits(s: String) = s.filter { it.isDigit() }

    private fun findRecording(ctx: Context, call: CallRow, end: Long, used: Set<String>): Rec? {
        val col = MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
        val pathCol = if (Build.VERSION.SDK_INT >= 29) MediaStore.Audio.Media.RELATIVE_PATH else MediaStore.Audio.Media.DATA
        val from = (call.start - 60_000) / 1000
        val to = (end + 15 * 60_000) / 1000
        val out = mutableListOf<Rec>()
        ctx.contentResolver.query(
            col,
            arrayOf(MediaStore.Audio.Media._ID, MediaStore.Audio.Media.DISPLAY_NAME, pathCol,
                MediaStore.Audio.Media.DATE_ADDED, MediaStore.Audio.Media.DURATION),
            "${MediaStore.Audio.Media.DATE_ADDED} BETWEEN ? AND ?", arrayOf(from.toString(), to.toString()), null
        )?.use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                if (id.toString() in used) continue
                out += Rec(id, ContentUris.withAppendedId(col, id), c.getString(1) ?: "", c.getString(2) ?: "",
                    c.getLong(3) * 1000, c.getLong(4))
            }
        }
        val num = digits(call.number).takeLast(7)
        fun looksLikeCall(r: Rec): Boolean {
            val s = (r.path + "/" + r.name).lowercase()
            return "call" in s || "مكالم" in s || "تسجيل" in s || "record" in s || (num.length >= 6 && num in digits(r.name))
        }
        return out.filter(::looksLikeCall)
            .filter { r -> r.durMs <= 0 || kotlin.math.abs(r.durMs / 1000 - call.durSec) <= maxOf(8, call.durSec / 5) }
            .minByOrNull { r ->
                var score = kotlin.math.abs(r.added - end).toDouble() / 1000
                if (num.length >= 6 && num in digits(r.name)) score -= 10_000
                score
            }
    }

    private fun upload(ctx: Context, host: String, ext: String, pass: String, call: CallRow, rec: Rec): Int {
        val conn = URL("https://$host/calls-app/upload").openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 20_000
            conn.readTimeout = 120_000
            conn.setChunkedStreamingMode(64 * 1024)
            conn.setRequestProperty("Content-Type", "application/octet-stream")
            conn.setRequestProperty("X-Ext", ext)
            conn.setRequestProperty("X-Pass", pass)
            conn.setRequestProperty("X-Number", call.number)
            conn.setRequestProperty("X-Start", call.start.toString())
            conn.setRequestProperty("X-Duration", call.durSec.toString())
            conn.setRequestProperty("X-Dir", if (call.incoming) "in" else "out")
            conn.setRequestProperty("X-Filename", "rec." + rec.name.substringAfterLast('.', "m4a").lowercase())
            ctx.contentResolver.openInputStream(rec.uri)?.use { input ->
                conn.outputStream.use { input.copyTo(it, 64 * 1024) }
            } ?: return 404
            conn.responseCode
        } catch (e: Exception) {
            -1
        } finally {
            conn.disconnect()
        }
    }
}
