// نغمة الرنين: نغمات جاهزة جوه التطبيق أو ملف من الجهاز
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:file_selector/file_selector.dart';
import 'package:path_provider/path_provider.dart';

import 'core.dart';

class Tone {
  final String id, name;
  const Tone(this.id, this.name);
}

class Ringer {
  Ringer._();
  static final Ringer I = Ringer._();

  static const tones = [
    Tone('classic', '☎️ كلاسيك'),
    Tone('dual', '🔔 رنة مزدوجة'),
    Tone('chime', '🎐 جرس هادي'),
    Tone('marimba', '🎵 ماريمبا'),
    Tone('digital', '📟 ديجيتال'),
    Tone('urgent', '🚨 عالي ومستعجل'),
  ];

  final AudioPlayer _p = AudioPlayer();
  bool _on = false;

  String get current => Prefs.str('ring', 'classic');
  String get customPath => Prefs.str('ringFile');
  String get customName => Prefs.str('ringFileName');
  double get volume => Prefs.dbl('ringVol', .8);

  String get currentName {
    if (current == 'custom' && customName.isNotEmpty) return '📂 $customName';
    if (current == 'silent') return '🔕 صامت';
    return tones.firstWhere((t) => t.id == current, orElse: () => tones.first).name;
  }

  Future<void> select(String id) => Prefs.setStr('ring', id);
  Future<void> setVolume(double v) async {
    await Prefs.setDbl('ringVol', v);
    try {
      await _p.setVolume(v);
    } catch (_) {}
  }

  Future<void> start([String? tone]) async {
    if (_on) return;
    _on = true;
    final current = tone ?? this.current;
    if (current == 'silent') return;
    try {
      await _p.setReleaseMode(ReleaseMode.loop);
      await _p.setVolume(volume);
      final f = customPath;
      if (current == 'custom' && f.isNotEmpty && File(f).existsSync()) {
        await _p.play(DeviceFileSource(f));
      } else {
        final id = tones.any((t) => t.id == current) ? current : 'classic';
        await _p.play(AssetSource('tones/$id.wav'));
      }
    } catch (e) {
      logd('ring error $e');
    }
  }

  /// نغمة "مكالمة في الانتظار": تيتين خفاف كل كام ثانية (من غير ما تغطي على صوت العميل)
  Future<void> startWaiting() async {
    if (_on) return;
    _on = true;
    try {
      await _p.setReleaseMode(ReleaseMode.loop);
      await _p.setVolume((volume * .6).clamp(.15, .6));
      await _p.play(AssetSource('tones/waiting.wav'));
    } catch (e) {
      logd('waiting tone $e');
    }
  }

  Future<void> stop() async {
    _on = false;
    try {
      await _p.stop();
    } catch (_) {}
  }

  /// تسمع النغمة 3 ثواني
  Future<void> preview(String id) async {
    await stop();
    await start(id);
    Future.delayed(const Duration(milliseconds: 3500), () {
      if (_on) stop();
    });
  }

  /// نغمة من الجهاز (mp3/wav/m4a...) — بننسخها جوه التطبيق عشان متضيعش
  Future<String?> pickCustom() async {
    const group = XTypeGroup(label: 'نغمات', extensions: ['mp3', 'wav', 'm4a', 'ogg', 'aac', 'flac'], mimeTypes: ['audio/*'], uniformTypeIdentifiers: ['public.audio']);
    final f = await openFile(acceptedTypeGroups: [group]);
    if (f == null) return null;
    final dir = await getApplicationSupportDirectory();
    final name = f.name;
    final ext = name.contains('.') ? name.substring(name.lastIndexOf('.')) : '.mp3';
    final dst = File('${dir.path}${Platform.pathSeparator}ringtone$ext');
    await dst.writeAsBytes(await f.readAsBytes(), flush: true);
    await Prefs.setStr('ringFile', dst.path);
    await Prefs.setStr('ringFileName', name);
    await select('custom');
    return name;
  }
}
