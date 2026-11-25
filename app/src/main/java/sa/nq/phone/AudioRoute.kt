package sa.nq.phone

import android.content.Context
import android.content.Intent
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import org.json.JSONObject

/**
 * مخرج صوت المكالمة: سماعة الموبايل / السبيكر / بلوتوث (العربية أو السماعات) / سماعة سلك.
 * - أول ما المكالمة تبدأ: لو فيه بلوتوث متوصل الصوت بيروح عليه لوحده.
 * - لو البلوتوث اتوصل أو اتفصل وسط المكالمة: بيحوّل لوحده.
 * - زرار السماعة/العربية (play/pause أو زرار الرد): يرد على المكالمة الواردة أو يقفل المكالمة.
 */
class AudioRoute(private val ctx: Context, private val toJs: (String) -> Unit) {
    private val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val h = Handler(Looper.getMainLooper())
    var current = "earpiece"; private set
    private var active = false
    private var userPicked = false
    private var session: MediaSession? = null

    private val cb = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>) {
            if (active && !userPicked && btName() != null && current != "bluetooth") h.postDelayed({ apply("bluetooth") }, 600) else notifyJs()
        }
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>) {
            if (active && current == "bluetooth" && btName() == null) apply(if (wired()) "wired" else "earpiece") else notifyJs()
        }
    }

    private fun isBt(t: Int) = t == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || t == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
        (Build.VERSION.SDK_INT >= 31 && (t == AudioDeviceInfo.TYPE_BLE_HEADSET || t == AudioDeviceInfo.TYPE_BLE_SPEAKER)) ||
        (Build.VERSION.SDK_INT >= 28 && t == AudioDeviceInfo.TYPE_HEARING_AID)

    private fun commDevices(): List<AudioDeviceInfo> =
        if (Build.VERSION.SDK_INT >= 31) am.availableCommunicationDevices else am.getDevices(AudioManager.GET_DEVICES_OUTPUTS).toList()

    fun btName(): String? = commDevices().firstOrNull { isBt(it.type) }?.let { (it.productName?.toString() ?: "").ifBlank { "بلوتوث" } }
    fun wired(): Boolean = commDevices().any { it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET || it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES || it.type == AudioDeviceInfo.TYPE_USB_HEADSET }

    fun json(): String = JSONObject().put("current", current).put("bt", btName() ?: JSONObject.NULL).put("wired", wired()).toString()
    private fun notifyJs() = toJs("window.NQPhone && NQPhone.onRoute && NQPhone.onRoute(${json()})")

    /** المكالمة بدأت / خلصت */
    fun callActive(on: Boolean) {
        if (on == active) { if (!on) mediaButtons(false); return }
        active = on
        if (on) {
            userPicked = false
            if (am.mode != AudioManager.MODE_IN_COMMUNICATION) am.mode = AudioManager.MODE_IN_COMMUNICATION
            am.isMicrophoneMute = false
            try { am.registerAudioDeviceCallback(cb, h) } catch (e: Exception) {}
            apply(if (btName() != null) "bluetooth" else if (wired()) "wired" else "earpiece")
            mediaButtons(true)
        } else {
            try { am.unregisterAudioDeviceCallback(cb) } catch (e: Exception) {}
            release()
            am.mode = AudioManager.MODE_NORMAL
            current = "earpiece"
            mediaButtons(false)
        }
    }

    /** الموظف اختار مخرج بنفسه */
    fun pick(kind: String) { userPicked = true; apply(kind) }

    fun apply(kind: String) {
        if (am.mode != AudioManager.MODE_IN_COMMUNICATION) am.mode = AudioManager.MODE_IN_COMMUNICATION
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                val d = commDevices()
                val dev = when (kind) {
                    "speaker" -> d.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                    "bluetooth" -> d.firstOrNull { isBt(it.type) }
                    "wired" -> d.firstOrNull { it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET || it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES || it.type == AudioDeviceInfo.TYPE_USB_HEADSET }
                    else -> d.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_EARPIECE }
                }
                if (dev != null) am.setCommunicationDevice(dev) else am.clearCommunicationDevice()
            } else {
                @Suppress("DEPRECATION")
                if (kind == "bluetooth") {
                    am.isSpeakerphoneOn = false
                    am.startBluetoothSco(); am.isBluetoothScoOn = true
                } else {
                    if (am.isBluetoothScoOn) { am.isBluetoothScoOn = false; am.stopBluetoothSco() }
                    am.isSpeakerphoneOn = kind == "speaker"
                }
            }
            current = kind
        } catch (e: Exception) {}
        am.isMicrophoneMute = false
        notifyJs()
    }

    private fun release() {
        try {
            if (Build.VERSION.SDK_INT >= 31) am.clearCommunicationDevice()
            else @Suppress("DEPRECATION") run {
                if (am.isBluetoothScoOn) { am.isBluetoothScoOn = false; am.stopBluetoothSco() }
                am.isSpeakerphoneOn = false
            }
        } catch (e: Exception) {}
    }

    /** أزرار السماعة/العربية: ضغطة = رد (لو بيرن) أو قفل (لو في مكالمة) */
    fun mediaButtons(on: Boolean) {
        try {
            if (on) {
                if (session == null) session = MediaSession(ctx, "NQPhone").apply {
                    setCallback(object : MediaSession.Callback() {
                        override fun onMediaButtonEvent(i: Intent): Boolean {
                            val k = if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_KEY_EVENT, KeyEvent::class.java)
                                    else @Suppress("DEPRECATION") i.getParcelableExtra(Intent.EXTRA_KEY_EVENT)
                            if (k != null && k.action == KeyEvent.ACTION_UP && k.keyCode in intArrayOf(
                                    KeyEvent.KEYCODE_HEADSETHOOK, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY,
                                    KeyEvent.KEYCODE_MEDIA_PAUSE, KeyEvent.KEYCODE_CALL, KeyEvent.KEYCODE_ENDCALL)) {
                                toJs("window.NQPhone && NQPhone.headsetButton && NQPhone.headsetButton()")
                                return true
                            }
                            return super.onMediaButtonEvent(i)
                        }
                    })
                    setPlaybackState(PlaybackState.Builder().setActions(PlaybackState.ACTION_PLAY_PAUSE or PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE)
                        .setState(PlaybackState.STATE_PLAYING, 0, 1f).build())
                }
                session?.isActive = true
            } else {
                session?.isActive = false
            }
        } catch (e: Exception) {}
    }
}
