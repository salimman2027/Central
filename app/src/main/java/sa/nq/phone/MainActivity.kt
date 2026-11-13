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

    override fun onBackPressed() { moveTaskToBack(true) } // الرجوع يصغّر بس، ميقفلش

    private fun askPermissions() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need += Manifest.permission.POST_NOTIFICATIONS
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

        @JavascriptInterface fun setSpeaker(on: Boolean) {
            @Suppress("DEPRECATION")
            (getSystemService(AUDIO_SERVICE) as AudioManager).isSpeakerphoneOn = on
        }
    }
}
