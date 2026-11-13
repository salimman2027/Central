package sa.nq.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator

/**
 * خدمة دايمة في الخلفية: بتخلّي أندرويد ميقفلش التطبيق وهو مقفول (Lock)،
 * فالاتصال بالسنترال يفضل شغّال، ولما تيجي مكالمة تنوّر الشاشة وترن.
 */
class PhoneService : Service() {

    companion object {
        const val CH_RUNNING = "nq_running"
        const val CH_CALL = "nq_incoming"
        const val ID_RUNNING = 1
        const val ID_CALL = 2
        const val ACTION_START = "start"
        const val ACTION_INCOMING = "incoming"
        const val ACTION_STOP_RING = "stop_ring"
        const val EXTRA_NUMBER = "number"

        @Volatile var instance: PhoneService? = null

        /** إشارات المكالمة بتروح للخدمة الشغّالة مباشرة (من غير ما نبدأها تاني من الخلفية) */
        fun incoming(number: String) = instance?.showIncoming(number)
        fun stopRing() = instance?.stopRinging()

        fun send(ctx: Context, action: String, number: String? = null) {
            val i = Intent(ctx, PhoneService::class.java).setAction(action)
            if (number != null) i.putExtra(EXTRA_NUMBER, number)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }
    }

    private var wake: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_RUNNING, "التليفون شغّال", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CH_CALL, "مكالمات واردة", NotificationManager.IMPORTANCE_HIGH).apply {
            setSound(null, null) // الرنة بنشغّلها إحنا
        })

        startAsForeground()

        wake = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "nq:phone").apply { setReferenceCounted(false); acquire() }
        @Suppress("DEPRECATION")
        wifi = (applicationContext.getSystemService(WIFI_SERVICE) as WifiManager)
            .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "nq:phone").apply { setReferenceCounted(false); acquire() }
    }

    private fun startAsForeground() {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = Notification.Builder(this, CH_RUNNING)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("NQ Phone")
            .setContentText("متصل بالسنترال وجاهز لاستقبال المكالمات")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34)
            startForeground(ID_RUNNING, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(ID_RUNNING, n)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startAsForeground() // أندرويد بيطلبها مع كل startForegroundService
        when (intent?.action) {
            ACTION_INCOMING -> showIncoming(intent.getStringExtra(EXTRA_NUMBER) ?: "")
            ACTION_STOP_RING -> stopRinging()
        }
        return START_STICKY
    }

    fun showIncoming(number: String) {
        val open = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val full = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val answer = PendingIntent.getActivity(this, 2,
            Intent(open).putExtra(MainActivity.EXTRA_ACTION, "answer"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val reject = PendingIntent.getActivity(this, 3,
            Intent(open).putExtra(MainActivity.EXTRA_ACTION, "reject"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        val n = Notification.Builder(this, CH_CALL)
            .setSmallIcon(android.R.drawable.sym_call_incoming)
            .setContentTitle("مكالمة واردة")
            .setContentText(number)
            .setCategory(Notification.CATEGORY_CALL)
            .setFullScreenIntent(full, true) // ينوّر الشاشة ويفتح التطبيق فوق الـ Lock
            .addAction(Notification.Action.Builder(null, "رد", answer).build())
            .addAction(Notification.Action.Builder(null, "رفض", reject).build())
            .setOngoing(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(ID_CALL, n)
        startRinging()
    }

    private fun startRinging() {
        if (ringtone?.isPlaying == true) return
        ringtone = RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE))?.apply {
            audioAttributes = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
            if (Build.VERSION.SDK_INT >= 28) isLooping = true
            play()
        }
        @Suppress("DEPRECATION")
        vibrator = (getSystemService(VIBRATOR_SERVICE) as Vibrator).apply {
            vibrate(VibrationEffect.createWaveform(longArrayOf(0, 800, 800), 0))
        }
    }

    fun stopRinging() {
        ringtone?.stop(); ringtone = null
        vibrator?.cancel(); vibrator = null
        getSystemService(NotificationManager::class.java).cancel(ID_CALL)
    }

    override fun onDestroy() {
        instance = null
        stopRinging()
        wake?.release(); wifi?.release()
        super.onDestroy()
    }
}
