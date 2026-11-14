package sa.nq.phone

import android.content.Context
import android.net.Uri
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** رفع ملف من الموبايل لمشاركة الملفات على السيرفر */
object FilesNet {
    fun saveCreds(ctx: Context, host: String, ext: String, pass: String) =
        ctx.getSharedPreferences("nq_creds", 0).edit().putString("host", host).putString("ext", ext).putString("pass", pass).apply()

    /** كل كام دقيقة: لو في ملفات جديدة → إشعار */
    fun checkNew(ctx: Context) {
        val p = ctx.getSharedPreferences("nq_creds", 0)
        val host = p.getString("host", "") ?: ""; val ext = p.getString("ext", "") ?: ""; val pass = p.getString("pass", "") ?: ""
        if (host.isBlank() || ext.isBlank()) return
        try {
            val c = URL("https://$host/calls-app/files/list").openConnection() as HttpURLConnection
            c.connectTimeout = 15_000; c.readTimeout = 20_000
            c.setRequestProperty("X-Ext", ext); c.setRequestProperty("X-Pass", pass)
            if (c.responseCode != 200) { c.disconnect(); return }
            val j = org.json.JSONObject(c.inputStream.bufferedReader().readText()); c.disconnect()
            val inbox = j.getJSONArray("inbox")
            val seen = p.getStringSet("notified", emptySet())!!.toMutableSet()
            for (k in 0 until inbox.length()) {
                val f = inbox.getJSONObject(k)
                val id = f.getString("id")
                if (f.optBoolean("seen") || id in seen) continue
                seen += id
                notify(ctx, id.hashCode(), "📁 ${f.optString("fromName").ifBlank { f.optString("from") }} بعتلك",
                    if (f.optString("kind") == "text") f.optString("text").take(120) else f.optString("name"))
            }
            p.edit().putStringSet("notified", seen.toList().takeLast(300).toSet()).apply()
        } catch (_: Exception) {}
    }

    private fun notify(ctx: Context, id: Int, title: String, text: String) {
        val nm = ctx.getSystemService(android.app.NotificationManager::class.java)
        nm.createNotificationChannel(android.app.NotificationChannel("files", "ملفات جديدة", android.app.NotificationManager.IMPORTANCE_DEFAULT))
        val open = android.app.PendingIntent.getActivity(ctx, 7, android.content.Intent(ctx, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_ACTION, "files"), android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(id, android.app.Notification.Builder(ctx, "files").setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(title).setContentText(text).setContentIntent(open).setAutoCancel(true).build())
    }

    fun upload(ctx: Context, host: String, ext: String, pass: String, to: String,
               uri: Uri, name: String, mime: String): String {
        val conn = URL("https://$host/calls-app/files/send").openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 20_000
            conn.readTimeout = 300_000
            conn.setChunkedStreamingMode(64 * 1024)
            conn.setRequestProperty("Content-Type", mime)
            conn.setRequestProperty("X-Ext", ext)
            conn.setRequestProperty("X-Pass", pass)
            conn.setRequestProperty("X-To", to)
            conn.setRequestProperty("X-Name", URLEncoder.encode(name, "UTF-8").replace("+", "%20"))
            ctx.contentResolver.openInputStream(uri)?.use { input ->
                conn.outputStream.use { input.copyTo(it, 64 * 1024) }
            }
            val code = conn.responseCode
            val body = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText() ?: ""
            org.json.JSONObject().put("code", code).put("body", body).toString()
        } catch (e: Exception) {
            org.json.JSONObject().put("code", -1).put("body", e.message ?: "").toString()
        } finally {
            conn.disconnect()
        }
    }
}
