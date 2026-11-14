package sa.nq.phone

import android.content.Context
import android.net.Uri
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** رفع ملف من الموبايل لمشاركة الملفات على السيرفر */
object FilesNet {
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
