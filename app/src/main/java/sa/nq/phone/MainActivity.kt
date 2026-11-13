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

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 2) {
            val n = pendingSimNumber ?: return
            pendingSimNumber = null
            val uri = Uri.parse("tel:" + Uri.encode(n))
            val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
            startActivity(Intent(if (granted) Intent.ACTION_CALL else Intent.ACTION_DIAL, uri))
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

        /** اتصال من شريحة الموبايل نفسه */
        @JavascriptInterface fun callWithSim(number: String) {
            runOnUiThread {
                val uri = Uri.parse("tel:" + Uri.encode(number))
                if (checkSelfPermission(Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED) {
                    startActivity(Intent(Intent.ACTION_CALL, uri))
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

        @JavascriptInterface fun setSpeaker(on: Boolean) {
            @Suppress("DEPRECATION")
            (getSystemService(AUDIO_SERVICE) as AudioManager).isSpeakerphoneOn = on
        }
    }
}
