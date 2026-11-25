package __PKG__

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/** خدمة بسيطة: إشعار ثابت "متصل بالسنترال" عشان أندرويد مايقفلش التطبيق والمكالمات توصل */
class NQService : Service() {
    private var lock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel("nq_run") == null) {
            nm.createNotificationChannel(NotificationChannel("nq_run", "متصل بالسنترال", NotificationManager.IMPORTANCE_LOW).apply { setShowBadge(false) })
        }
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, "nq_run") else @Suppress("DEPRECATION") Notification.Builder(this)
        val n = b.setSmallIcon(android.R.drawable.stat_sys_phone_call)
            .setContentTitle("NQ Call")
            .setContentText(intent?.getStringExtra("text")?.ifBlank { null } ?: "متصل بالسنترال — المكالمات هتوصلك")
            .setContentIntent(pi).setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(1, n)
        if (lock == null) {
            // قفل خفيف للمعالج بس (مش الشاشة) عشان الاتصال بالسنترال مايتقطعش والموبايل نايم
            lock = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "nq:line").apply { setReferenceCounted(false); acquire() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        try { lock?.release() } catch (_: Exception) {}
        lock = null
        super.onDestroy()
    }
}
