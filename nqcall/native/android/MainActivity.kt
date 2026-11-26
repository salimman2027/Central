package __PKG__

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    companion object {
        const val CH_CALL = "nq_incoming"
        const val ID_CALL = 7
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        wakeIfIncoming(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        wakeIfIncoming(intent)
    }

    /** لما المكالمة الواردة تفتح التطبيق: يظهر فوق قفل الشاشة وينوّرها */
    private fun wakeIfIncoming(i: Intent?) {
        if (i?.getBooleanExtra("incoming", false) != true) return
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nq/native").setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "startService" -> {
                        val i = Intent(this, NQService::class.java).putExtra("text", call.argument<String>("text") ?: "")
                        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
                        result.success(true)
                    }
                    "simCall" -> {
                        val n = call.argument<String>("number") ?: ""
                        val u = Uri.fromParts("tel", n, null)
                        val canCall = checkSelfPermission(android.Manifest.permission.CALL_PHONE) == android.content.pm.PackageManager.PERMISSION_GRANTED
                        startActivity(Intent(if (canCall) Intent.ACTION_CALL else Intent.ACTION_DIAL, u).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                        result.success(true)
                    }
                    "inSimCall" -> {
                        val am = getSystemService(android.media.AudioManager::class.java)
                        result.success(am.mode == android.media.AudioManager.MODE_IN_CALL)
                    }
                    "stopService" -> { stopService(Intent(this, NQService::class.java)); result.success(true) }
                    "incoming" -> { showIncoming(call.argument<String>("title") ?: "مكالمة واردة", call.argument<String>("body") ?: ""); result.success(true) }
                    "cancelIncoming" -> {
                        getSystemService(NotificationManager::class.java).cancel(ID_CALL)
                        if (Build.VERSION.SDK_INT >= 27) setShowWhenLocked(false)
                        result.success(true)
                    }
                    "batteryExempt" -> {
                        val pm = getSystemService(PowerManager::class.java)
                        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                            @Suppress("BatteryLife")
                            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
                        } else startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            } catch (e: Exception) { result.error("ERR", e.message, null) }
        }
    }

    /** إشعار بملء الشاشة: لو الموبايل مقفول أو التطبيق ورا، الشاشة بتنوّر والتطبيق يظهر */
    private fun showIncoming(title: String, body: String) {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CH_CALL) == null) {
            nm.createNotificationChannel(NotificationChannel(CH_CALL, "مكالمات واردة", NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(null, null) // النغمة بيشغّلها التطبيق نفسه (اللي الموظف اختارها)
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 800, 800, 800)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
            })
        }
        val open = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("incoming", true)
        val pi = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = if (Build.VERSION.SDK_INT >= 26) android.app.Notification.Builder(this, CH_CALL) else @Suppress("DEPRECATION") android.app.Notification.Builder(this)
        val n = b.setSmallIcon(android.R.drawable.sym_call_incoming)
            .setContentTitle(title).setContentText(body)
            .setCategory(android.app.Notification.CATEGORY_CALL)
            .setFullScreenIntent(pi, true)
            .setContentIntent(pi)
            .setOngoing(true)
            .setAutoCancel(true)
            .build()
        nm.notify(ID_CALL, n)
    }
}
