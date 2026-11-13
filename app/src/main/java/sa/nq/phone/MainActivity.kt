package sa.nq.phone

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient

class MainActivity : Activity() {

    companion object { const val EXTRA_ACTION = "nq_action" }

    private lateinit var web: WebView
    private var pendingSimNumber: String? = null

    @Deprecated("startActivityForResult is enough for a contact pick")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 4) { // كلام → نص
            val text = if (resultCode == RESULT_OK)
                data?.getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS)?.firstOrNull() ?: "" else ""
            web.evaluateJavascript("window.onDictation && onDictation(" + org.json.JSONObject.quote(text) + ")", null)
            return
        }
        if (requestCode != 3 || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        var name = ""; var number = ""
        contentResolver.query(uri, arrayOf(
            android.provider.ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            android.provider.ContactsContract.CommonDataKinds.Phone.NUMBER), null, null, null)?.use { c ->
            if (c.moveToFirst()) { name = c.getString(0) ?: ""; number = c.getString(1) ?: "" }
        }
        val js = "window.onContactPicked && onContactPicked(" + org.json.JSONObject.quote(name) + "," + org.json.JSONObject.quote(number) + ")"
        web.evaluateJavascript(js, null)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 2) {
            val n = pendingSimNumber ?: return
            pendingSimNumber = null
            val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
            if (granted) placeSimCall(n)
            else startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(n))))
        }
    }

    /** اتصال مباشر من شريحة الموبايل عن طريق نظام الاتصال نفسه (من غير ما يسأل أنهي تطبيق) */
    @SuppressLint("MissingPermission")
    private fun placeSimCall(number: String) {
        val uri = Uri.fromParts("tel", number, null)
        try {
            val tm = getSystemService(android.telecom.TelecomManager::class.java)
            tm.placeCall(uri, Bundle())
        } catch (e: Exception) {
            val i = Intent(Intent.ACTION_CALL, uri)
            val dialer = getSystemService(android.telecom.TelecomManager::class.java)?.defaultDialerPackage
            if (dialer != null) i.setPackage(dialer)
            startActivity(i)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }

        web = WebView(this)
        setContentView(web)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
        }
        // يخلّي صفحة التليفون شغّالة بأولوية عالية حتى والشاشة مقفولة
        if (Build.VERSION.SDK_INT >= 26) web.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false)
        web.webViewClient = WebViewClient()
        web.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
            }
        }
        web.addJavascriptInterface(Bridge(), "Android")
        web.loadUrl("file:///android_asset/phone.html")

        askPermissions()
        PhoneService.send(this, PhoneService.ACTION_START)
        handleAction(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleAction(intent)
    }

    private fun handleAction(i: Intent?) {
        when (i?.getStringExtra(EXTRA_ACTION)) {
            "answer" -> web.post { web.evaluateJavascript("NQPhone.answer()", null) }
            "reject" -> web.post { web.evaluateJavascript("NQPhone.hangup()", null) }
        }
        i?.removeExtra(EXTRA_ACTION)
    }

    // متوقفش الصفحة لما الشاشة تتقفل
    override fun onPause() { super.onPause(); web.resumeTimers() }

    // زرار الرجوع: يسأل تخرج ولا لأ
    override fun onBackPressed() {
        web.evaluateJavascript("(window.NQPhone && NQPhone.session) ? 1 : 0") { inCall ->
            val msg = if (inCall == "1") "في مكالمة شغّالة. لو خرجت المكالمة هتقفل ومش هتستقبل مكالمات."
                      else "لو خرجت مش هتستقبل مكالمات لحد ما تفتح التطبيق تاني."
            android.app.AlertDialog.Builder(this)
                .setTitle("تخرج من NQ Phone؟")
                .setMessage(msg)
                .setPositiveButton("خروج") { _, _ -> exitApp() }
                .setNeutralButton("تصغير (يفضل يستقبل)") { _, _ -> moveTaskToBack(true) }
                .setNegativeButton("إلغاء", null)
                .show()
        }
    }

    private fun exitApp() {
        web.evaluateJavascript("try{NQPhone.hangup();NQPhone.ua&&NQPhone.ua.stop()}catch(e){}", null)
        web.postDelayed({
            stopService(Intent(this, PhoneService::class.java))
            finishAndRemoveTask()
        }, 400)
    }

    private fun askPermissions() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need += Manifest.permission.POST_NOTIFICATIONS
        if (Build.VERSION.SDK_INT <= 28) need += Manifest.permission.WRITE_EXTERNAL_STORAGE
        val missing = need.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), 1)

        val pm = getSystemService(POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        }
        if (Build.VERSION.SDK_INT >= 34) {
            val nm = getSystemService(NotificationManager::class.java)
            if (!nm.canUseFullScreenIntent()) {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:$packageName")))
            }
        }
    }

    /** صفحة التليفون بتكلّم التطبيق من هنا */
    private var pdfView: WebView? = null

    private fun writeDownload(name: String, mime: String, bytes: ByteArray): Uri {
        if (Build.VERSION.SDK_INT >= 29) {
            val v = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Downloads.DISPLAY_NAME, name)
                put(android.provider.MediaStore.Downloads.MIME_TYPE, mime)
            }
            val uri = contentResolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)!!
            contentResolver.openOutputStream(uri)!!.use { it.write(bytes) }
            return uri
        }
        @Suppress("DEPRECATION")
        val dir = android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS)
        dir.mkdirs()
        val f = java.io.File(dir, name); f.writeBytes(bytes)
        return Uri.fromFile(f)
    }

    private fun openFile(uri: Uri, mime: String) {
        try {
            if (uri.scheme == "file") {
                @Suppress("DEPRECATION")
                android.os.StrictMode.setVmPolicy(android.os.StrictMode.VmPolicy.Builder().build())
            }
            val i = Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(Intent.createChooser(i, "فتح الملف").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            android.widget.Toast.makeText(this, "الملف في التنزيلات — مفيش برنامج يفتحه", android.widget.Toast.LENGTH_LONG).show()
        }
    }

    private fun makePdf(name: String, html: String) {
        val v = WebView(this)
        pdfView = v
        v.settings.javaScriptEnabled = false
        v.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String?) {
                val attrs = android.print.PrintAttributes.Builder()
                    .setMediaSize(android.print.PrintAttributes.MediaSize.ISO_A4.asLandscape())
                    .setResolution(android.print.PrintAttributes.Resolution("pdf", "pdf", 600, 600))
                    .setMinMargins(android.print.PrintAttributes.Margins.NO_MARGINS).build()
                val tmp = java.io.File(cacheDir, "report.pdf")
                android.print.PdfPrint(attrs).print(view.createPrintDocumentAdapter(name), tmp) { ok ->
                    runOnUiThread {
                        var saved = false
                        if (ok) try {
                            val uri = writeDownload(name, "application/pdf", tmp.readBytes())
                            saved = true
                            openFile(uri, "application/pdf")
                        } catch (_: Exception) {}
                        web.evaluateJavascript("window.onFileSaved && onFileSaved($saved)", null)
                        pdfView?.destroy(); pdfView = null
                    }
                }
            }
        }
        v.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    inner class Bridge {
        @JavascriptInterface fun onIncoming(number: String) {
            if (PhoneService.instance != null) PhoneService.incoming(number)
            else PhoneService.send(this@MainActivity, PhoneService.ACTION_INCOMING, number)
        }

        @JavascriptInterface fun onCallStateChanged(active: Boolean) {
            PhoneService.stopRing()
            val am = getSystemService(AUDIO_SERVICE) as AudioManager
            am.mode = if (active) AudioManager.MODE_IN_COMMUNICATION else AudioManager.MODE_NORMAL
        }

        /** يسجّل كلام بالمايك ويحوّله لنص عربي */
        @JavascriptInterface fun dictate(lang: String) {
            runOnUiThread {
                val i = Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL, android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, lang)
                    .putExtra(android.speech.RecognizerIntent.EXTRA_PROMPT, "قول ملخص المكالمة")
                try {
                    @Suppress("DEPRECATION")
                    startActivityForResult(i, 4)
                } catch (e: Exception) {
                    web.evaluateJavascript("window.onDictation && onDictation(null)", null)
                }
            }
        }

        /** يفتح جهات الاتصال ويرجّع الاسم والرقم */
        @JavascriptInterface fun pickContact() {
            runOnUiThread {
                @Suppress("DEPRECATION")
                startActivityForResult(Intent(Intent.ACTION_PICK,
                    android.provider.ContactsContract.CommonDataKinds.Phone.CONTENT_URI), 3)
            }
        }

        /** اتصال من شريحة الموبايل نفسه */
        @JavascriptInterface fun callWithSim(number: String) {
            runOnUiThread {
                if (checkSelfPermission(Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED) {
                    placeSimCall(number)
                } else {
                    pendingSimNumber = number
                    requestPermissions(arrayOf(Manifest.permission.CALL_PHONE), 2)
                }
            }
        }

        /** يحفظ التقرير في فولدر التنزيلات */
        @JavascriptInterface fun saveFile(name: String, content: String): String {
            return try {
                val bytes = content.toByteArray(Charsets.UTF_8)
                if (Build.VERSION.SDK_INT >= 29) {
                    val v = android.content.ContentValues().apply {
                        put(android.provider.MediaStore.Downloads.DISPLAY_NAME, name)
                        put(android.provider.MediaStore.Downloads.MIME_TYPE, "text/csv")
                    }
                    val uri = contentResolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)!!
                    contentResolver.openOutputStream(uri)!!.use { it.write(bytes) }
                } else {
                    @Suppress("DEPRECATION")
                    val dir = android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS)
                    java.io.File(dir, name).writeBytes(bytes)
                }
                "Download/$name"
            } catch (e: Exception) { "" }
        }

        @JavascriptInterface fun saveAndOpen(name: String, mime: String, b64: String): Boolean = try {
            val bytes = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
            val uri = writeDownload(name, mime, bytes)
            runOnUiThread { openFile(uri, mime) }
            true
        } catch (e: Exception) { false }

        @JavascriptInterface fun savePdf(name: String, html: String) {
            runOnUiThread { makePdf(name, html) }
        }

        @JavascriptInterface fun setSpeaker(on: Boolean) {
            @Suppress("DEPRECATION")
            (getSystemService(AUDIO_SERVICE) as AudioManager).isSpeakerphoneOn = on
        }
    }
}
