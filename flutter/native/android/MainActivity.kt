package __PKG__

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/** قناة nq/audio: وضع المكالمة والسبيكر (أندرويد 12+ بـ setCommunicationDevice عشان المايك مايقفلش) */
class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nq/audio").setMethodCallHandler { call, result ->
            val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val on = call.arguments as? Boolean ?: false
            when (call.method) {
                "call" -> {
                    if (on) { am.mode = AudioManager.MODE_IN_COMMUNICATION; am.isMicrophoneMute = false }
                    else { route(am, false); am.mode = AudioManager.MODE_NORMAL }
                    result.success(null)
                }
                "speaker" -> {
                    if (am.mode != AudioManager.MODE_IN_COMMUNICATION) am.mode = AudioManager.MODE_IN_COMMUNICATION
                    route(am, on); am.isMicrophoneMute = false
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun route(am: AudioManager, speaker: Boolean) {
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                if (speaker) am.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }?.let { am.setCommunicationDevice(it) }
                else am.clearCommunicationDevice()
            } else {
                @Suppress("DEPRECATION") am.isSpeakerphoneOn = speaker
            }
        } catch (e: Exception) {
            @Suppress("DEPRECATION") am.isSpeakerphoneOn = speaker
        }
    }
}
