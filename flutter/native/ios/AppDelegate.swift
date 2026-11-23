import Flutter
import UIKit
import AVFoundation

/// قناة nq/audio: جلسة الصوت للمكالمة (playAndRecord/voiceChat)، السبيكر، وحساس القرب
@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(_ application: UIApplication,
                            didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
    GeneratedPluginRegistrant.register(with: self)
    if let reg = self.registrar(forPlugin: "NQAudio") {
      let ch = FlutterMethodChannel(name: "nq/audio", binaryMessenger: reg.messenger())
      ch.setMethodCallHandler { call, result in
        let s = AVAudioSession.sharedInstance()
        let on = (call.arguments as? Bool) ?? false
        switch call.method {
        case "call":
          UIDevice.current.isProximityMonitoringEnabled = on
          if on {
            try? s.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .allowBluetoothA2DP])
            try? s.setActive(true)
          } else {
            try? s.overrideOutputAudioPort(.none)
            try? s.setActive(false, options: .notifyOthersOnDeactivation)
          }
          result(nil)
        case "speaker":
          if s.category != .playAndRecord {
            try? s.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .allowBluetoothA2DP])
          }
          try? s.setActive(true)
          try? s.overrideOutputAudioPort(on ? .speaker : .none)
          result(nil)
        default:
          result(FlutterMethodNotImplemented)
        }
      }
    }
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
}
