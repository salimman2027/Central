package sa.nq.phone

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.provider.MediaStore
import android.provider.OpenableColumns
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.MulticastSocket
import java.net.ServerSocket
import java.net.Socket
import java.net.URL
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * إرسال واستقبال ملفات مباشرة على نفس الواي فاي — متوافق مع بروتوكول LocalSend v2،
 * يعني NQ Phone يقدر يبعت ويستقبل من برنامج LocalSend على اللابتوب أو أي موبايل.
 */
class LocalNet(private val ctx: Context, private val emit: (String, String) -> Unit) {
    companion object {
        const val PORT = 53317
        const val GROUP = "224.0.0.167"
        private const val API = "/api/localsend/v2"
    }

    data class Device(val fingerprint: String, val alias: String, val ip: String, val port: Int,
                      val protocol: String, val model: String, val type: String, var seen: Long)

    data class InFile(val id: String, val name: String, val size: Long, val mime: String, val token: String)
    data class Session(val id: String, val from: String, val files: Map<String, InFile>, var done: Int = 0)

    private val fingerprint: String = ctx.getSharedPreferences("nq_local", 0).let { p ->
        p.getString("fp", null) ?: UUID.randomUUID().toString().replace("-", "").also { p.edit().putString("fp", it).apply() }
    }
    var alias = "NQ Phone"
    @Volatile var receiving = false
    @Volatile private var running = false
    private var server: ServerSocket? = null
    private var mcast: MulticastSocket? = null
    private var lock: WifiManager.MulticastLock? = null
    val devices = ConcurrentHashMap<String, Device>()
    private val sessions = ConcurrentHashMap<String, Session>()

    private fun info(withPort: Boolean = true) = JSONObject()
        .put("alias", alias).put("version", "2.0")
        .put("deviceModel", Build.MANUFACTURER.replaceFirstChar { it.uppercase() } + " " + Build.MODEL)
        .put("deviceType", "mobile").put("fingerprint", fingerprint).put("download", false)
        .apply { if (withPort) put("port", PORT).put("protocol", "http") }

    // ------------------------------------------------------------------ start / stop
    fun start() {
        if (running) { announce(); return }
        running = true
        lock = (ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
            .createMulticastLock("nq:localsend").apply { setReferenceCounted(false); acquire() }
        Thread(::serve, "nq-local-http").start()
        Thread(::listen, "nq-local-mcast").start()
        Thread { Thread.sleep(400); announce() }.start()
    }

    fun stop() {
        running = false
        try { server?.close() } catch (_: Exception) {}
        try { mcast?.close() } catch (_: Exception) {}
        lock?.release()
        devices.clear()
    }

    fun announce() = Thread {
        repeat(3) { i ->
            try {
                val data = info().put("announce", true).toString().toByteArray()
                java.net.DatagramSocket().use { it.send(DatagramPacket(data, data.size, InetAddress.getByName(GROUP), PORT)) }
            } catch (_: Exception) {}
            Thread.sleep(if (i == 0) 300 else 900)
        }
    }.start()

    // ------------------------------------------------------------------ discovery
    private fun listen() {
        try {
            val s = MulticastSocket(null).apply {
                reuseAddress = true
                bind(InetSocketAddress(PORT))
                @Suppress("DEPRECATION") joinGroup(InetAddress.getByName(GROUP))
            }
            mcast = s
            val buf = ByteArray(8192)
            while (running) {
                val p = DatagramPacket(buf, buf.size)
                s.receive(p)
                try {
                    val j = JSONObject(String(p.data, 0, p.length))
                    val ip = p.address.hostAddress ?: continue
                    if (j.optString("fingerprint") == fingerprint) continue
                    addDevice(j, ip)
                    if (j.optBoolean("announce")) reply(j, ip)
                } catch (_: Exception) {}
            }
        } catch (_: Exception) {
        }
    }

    private fun addDevice(j: JSONObject, ip: String) {
        val fp = j.optString("fingerprint").ifBlank { ip }
        devices[fp] = Device(fp, j.optString("alias", ip), ip, j.optInt("port", PORT),
            j.optString("protocol", "https"), j.optString("deviceModel", ""), j.optString("deviceType", ""),
            System.currentTimeMillis())
        pushDevices()
    }

    fun pushDevices() {
        val now = System.currentTimeMillis()
        devices.values.removeIf { now - it.seen > 10 * 60_000 }
        val arr = JSONArray()
        devices.values.sortedBy { it.alias }.forEach {
            arr.put(JSONObject().put("fp", it.fingerprint).put("alias", it.alias).put("ip", it.ip)
                .put("model", it.model).put("type", it.type))
        }
        emit("onLocalDevices", arr.toString())
    }

    /** بنرد على اللي عمل announce عن طريق /register، ولو فشل بنعمل multicast */
    private fun reply(j: JSONObject, ip: String) = Thread {
        val ok = try {
            val c = open("${j.optString("protocol", "https")}://$ip:${j.optInt("port", PORT)}$API/register")
            c.requestMethod = "POST"; c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(info().toString().toByteArray()) }
            val code = c.responseCode; c.disconnect(); code in 200..299
        } catch (_: Exception) { false }
        if (!ok) try {
            val data = info().put("announce", false).toString().toByteArray()
            java.net.DatagramSocket().use { it.send(DatagramPacket(data, data.size, InetAddress.getByName(GROUP), PORT)) }
        } catch (_: Exception) {}
    }.start()

    // ------------------------------------------------------------------ HTTP server (receive)
    private fun serve() {
        try {
            val ss = ServerSocket().apply { reuseAddress = true; bind(InetSocketAddress(PORT)) }
            server = ss
            while (running) {
                val sock = ss.accept()
                Thread { try { handle(sock) } catch (_: Exception) {} finally { try { sock.close() } catch (_: Exception) {} } }.start()
            }
        } catch (_: Exception) {
        }
    }

    private class Req(val method: String, val path: String, val query: Map<String, String>,
                      val headers: Map<String, String>, val input: InputStream)

    private fun readLine(i: InputStream): String? {
        val sb = StringBuilder()
        while (true) {
            val b = i.read()
            if (b == -1) return if (sb.isEmpty()) null else sb.toString()
            if (b == '\n'.code) return sb.toString().trimEnd('\r')
            sb.append(b.toChar())
        }
    }

    private fun parse(i: InputStream): Req? {
        val line = readLine(i) ?: return null
        val parts = line.split(" ")
        if (parts.size < 2) return null
        val h = HashMap<String, String>()
        while (true) {
            val l = readLine(i) ?: break
            if (l.isEmpty()) break
            val k = l.indexOf(':')
            if (k > 0) h[l.substring(0, k).trim().lowercase()] = l.substring(k + 1).trim()
        }
        val full = parts[1]
        val q = HashMap<String, String>()
        full.substringAfter('?', "").split('&').filter { it.contains('=') }.forEach {
            q[java.net.URLDecoder.decode(it.substringBefore('='), "UTF-8")] = java.net.URLDecoder.decode(it.substringAfter('='), "UTF-8")
        }
        return Req(parts[0].uppercase(), full.substringBefore('?'), q, h, i)
    }

    /** الـ body: بطول Content-Length أو chunked */
    private fun body(r: Req): InputStream {
        val len = r.headers["content-length"]?.toLongOrNull()
        if (len != null) return Limited(r.input, len)
        if (r.headers["transfer-encoding"]?.contains("chunked", true) == true) return Chunked(r.input, ::readLine)
        return Limited(r.input, 0)
    }

    private fun send(out: OutputStream, code: Int, json: String? = null) {
        val msg = mapOf(200 to "OK", 204 to "No Content", 400 to "Bad Request", 403 to "Forbidden",
            404 to "Not Found", 409 to "Conflict", 500 to "Internal Server Error")[code] ?: "OK"
        val b = json?.toByteArray() ?: ByteArray(0)
        out.write(("HTTP/1.1 $code $msg\r\nContent-Type: application/json\r\nContent-Length: ${b.size}\r\n" +
                "Connection: close\r\n\r\n").toByteArray())
        out.write(b); out.flush()
    }

    private fun handle(sock: Socket) {
        sock.soTimeout = 60_000
        val inp = BufferedInputStream(sock.getInputStream(), 64 * 1024)
        val out = sock.getOutputStream()
        val r = parse(inp) ?: return
        val ip = sock.inetAddress.hostAddress ?: ""
        when {
            r.path == "$API/register" && r.method == "POST" -> {
                try { addDevice(JSONObject(body(r).readBytes().toString(Charsets.UTF_8)), ip) } catch (_: Exception) {}
                send(out, 200, info(false).toString())
            }
            r.path == "$API/info" -> send(out, 200, info(false).toString())
            r.path == "$API/prepare-upload" && r.method == "POST" -> prepare(r, out, ip)
            r.path == "$API/upload" && r.method == "POST" -> upload(r, out)
            r.path == "$API/cancel" -> { sessions.remove(r.query["sessionId"]); send(out, 200) }
            else -> send(out, 404)
        }
    }

    private fun prepare(r: Req, out: OutputStream, ip: String) {
        if (!receiving) { send(out, 403); return }
        val j = try { JSONObject(body(r).readBytes().toString(Charsets.UTF_8)) } catch (_: Exception) { send(out, 400); return }
        val from = j.optJSONObject("info")?.optString("alias") ?: ip
        val files = j.optJSONObject("files") ?: run { send(out, 400); return }
        val map = LinkedHashMap<String, InFile>()
        val tokens = JSONObject()
        val list = JSONArray()
        files.keys().forEach { k ->
            val f = files.getJSONObject(k)
            val tok = UUID.randomUUID().toString()
            map[k] = InFile(k, f.optString("fileName", k), f.optLong("size"), f.optString("fileType", "application/octet-stream"), tok)
            tokens.put(k, tok)
            list.put(JSONObject().put("name", f.optString("fileName", k)).put("size", f.optLong("size")))
        }
        val sid = UUID.randomUUID().toString()
        sessions[sid] = Session(sid, from, map)
        emit("onLocalIncoming", JSONObject().put("from", from).put("files", list).toString())
        send(out, 200, JSONObject().put("sessionId", sid).put("files", tokens).toString())
    }

    private fun upload(r: Req, out: OutputStream) {
        val s = sessions[r.query["sessionId"]] ?: run { send(out, 403); return }
        val f = s.files[r.query["fileId"]] ?: run { send(out, 400); return }
        if (f.token != r.query["token"]) { send(out, 403); return }
        try {
            val uri = newDownload(f.name, f.mime)
            ctx.contentResolver.openOutputStream(uri)!!.use { o -> body(r).copyTo(o, 64 * 1024) }
            finishDownload(uri)
            s.done++
            emit("onLocalReceived", JSONObject().put("name", f.name).put("from", s.from).put("uri", uri.toString())
                .put("mime", f.mime).put("left", s.files.size - s.done).toString())
            if (s.done >= s.files.size) sessions.remove(s.id)
            send(out, 200)
        } catch (e: Exception) {
            send(out, 500)
        }
    }

    private fun newDownload(name: String, mime: String): Uri {
        if (Build.VERSION.SDK_INT >= 29) {
            val v = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, mime)
                put(MediaStore.Downloads.RELATIVE_PATH, "Download/NQ")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            return ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)!!
        }
        @Suppress("DEPRECATION")
        val dir = java.io.File(android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS), "NQ")
        dir.mkdirs()
        return Uri.fromFile(java.io.File(dir, name))
    }

    private fun finishDownload(uri: Uri) {
        if (Build.VERSION.SDK_INT >= 29 && uri.scheme == "content")
            ctx.contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
    }

    // ------------------------------------------------------------------ send
    private val trustAll: SSLContext by lazy {
        SSLContext.getInstance("TLS").apply {
            init(null, arrayOf<TrustManager>(object : X509TrustManager {
                override fun checkClientTrusted(c: Array<java.security.cert.X509Certificate>, a: String) {}
                override fun checkServerTrusted(c: Array<java.security.cert.X509Certificate>, a: String) {}
                override fun getAcceptedIssuers() = arrayOf<java.security.cert.X509Certificate>()
            }), java.security.SecureRandom())
        }
    }

    /** أجهزة LocalSend بتستخدم شهادة self-signed على الشبكة المحلية — بنقبلها للاتصالات دي بس */
    private fun open(url: String): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        if (c is HttpsURLConnection) { c.sslSocketFactory = trustAll.socketFactory; c.setHostnameVerifier { _, _ -> true } }
        c.connectTimeout = 8_000; c.readTimeout = 120_000
        return c
    }

    fun sendFiles(fp: String, uris: List<Uri>) = Thread {
        val d = devices[fp]
        if (d == null) { emit("onLocalSent", JSONObject().put("ok", false).put("error", "الجهاز مش ظاهر — دوس تحديث").toString()); return@Thread }
        try {
            val files = JSONObject()
            val meta = HashMap<String, Triple<Uri, String, Long>>()
            uris.forEachIndexed { i, u ->
                var name = "file$i"; var size = 0L
                ctx.contentResolver.query(u, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
                    if (c.moveToFirst()) { name = c.getString(0) ?: name; size = c.getLong(1) }
                }
                val mime = ctx.contentResolver.getType(u) ?: "application/octet-stream"
                val id = "f$i"
                files.put(id, JSONObject().put("id", id).put("fileName", name).put("size", size).put("fileType", mime))
                meta[id] = Triple(u, name, size)
            }
            val base = "${d.protocol}://${d.ip}:${d.port}$API"
            emit("onLocalProgress", JSONObject().put("text", "مستني ${d.alias} يقبل…").toString())
            val c = open("$base/prepare-upload")
            c.requestMethod = "POST"; c.doOutput = true; c.readTimeout = 120_000
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(JSONObject().put("info", info()).put("files", files).toString().toByteArray()) }
            val code = c.responseCode
            if (code == 204) { emit("onLocalSent", JSONObject().put("ok", true).put("count", 0).toString()); return@Thread }
            if (code != 200) {
                val why = mapOf(403 to "الجهاز التاني رفض", 401 to "الجهاز التاني طالب PIN", 409 to "الجهاز مشغول بإرسال تاني")[code] ?: "خطأ $code"
                emit("onLocalSent", JSONObject().put("ok", false).put("error", why).toString()); return@Thread
            }
            val resp = JSONObject(c.inputStream.bufferedReader().readText()); c.disconnect()
            val sid = resp.getString("sessionId")
            val tokens = resp.getJSONObject("files")
            var n = 0
            tokens.keys().forEach { id ->
                val (u, name, size) = meta[id] ?: return@forEach
                n++
                emit("onLocalProgress", JSONObject().put("text", "بيبعت $name ($n/${tokens.length()})").toString())
                val up = open("$base/upload?sessionId=$sid&fileId=$id&token=${tokens.getString(id)}")
                up.requestMethod = "POST"; up.doOutput = true
                if (size > 0) up.setFixedLengthStreamingMode(size) else up.setChunkedStreamingMode(64 * 1024)
                up.setRequestProperty("Content-Type", "application/octet-stream")
                ctx.contentResolver.openInputStream(u)!!.use { i -> up.outputStream.use { i.copyTo(it, 64 * 1024) } }
                val uc = up.responseCode; up.disconnect()
                if (uc !in 200..299) throw RuntimeException("الإرسال وقف عند $name (كود $uc)")
            }
            emit("onLocalSent", JSONObject().put("ok", true).put("count", n).put("to", d.alias).toString())
        } catch (e: Exception) {
            emit("onLocalSent", JSONObject().put("ok", false).put("error", e.message ?: "تعذّر الإرسال").toString())
        }
    }.start()
}

/** InputStream بطول محدد (Content-Length) */
private class Limited(private val i: InputStream, private var left: Long) : InputStream() {
    override fun read(): Int { if (left <= 0) return -1; val b = i.read(); if (b >= 0) left--; return b }
    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (left <= 0) return -1
        val n = i.read(b, off, minOf(len.toLong(), left).toInt()); if (n > 0) left -= n; return n
    }
}

/** InputStream لـ Transfer-Encoding: chunked */
private class Chunked(private val i: InputStream, private val line: (InputStream) -> String?) : InputStream() {
    private var left = 0L
    private var eof = false
    private fun next(): Boolean {
        if (eof) return false
        if (left == 0L) {
            var l = line(i) ?: run { eof = true; return false }
            if (l.isEmpty()) l = line(i) ?: run { eof = true; return false }
            left = l.substringBefore(';').trim().toLong(16)
            if (left == 0L) { eof = true; line(i); return false }
        }
        return true
    }
    override fun read(): Int { if (!next()) return -1; val b = i.read(); if (b >= 0) left--; return b }
    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (!next()) return -1
        val n = i.read(b, off, minOf(len.toLong(), left).toInt()); if (n > 0) left -= n; return n
    }
}
