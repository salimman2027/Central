import 'package:flutter/material.dart';

import '../core.dart';
import '../phone.dart';
import '../win.dart';
import 'theme.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _user = TextEditingController(text: Prefs.str('lastUser'));
  final _pass = TextEditingController();
  bool _busy = false, _hide = true;
  String _err = '';

  Future<void> _go() async {
    if (_user.text.trim().isEmpty || _pass.text.isEmpty) {
      setState(() => _err = 'اكتب اسم المستخدم وكلمة السر');
      return;
    }
    setState(() {
      _busy = true;
      _err = '';
    });
    try {
      final a = await Api.login(_user.text, _pass.text);
      await Prefs.setStr('lastUser', _user.text.trim());
      await PhoneEngine.I.signIn(a);
    } catch (e) {
      if (mounted) setState(() => _err = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(children: [
        Container(
          decoration: const BoxDecoration(gradient: kHeaderGradient),
          child: SafeArea(
            bottom: false,
            child: Column(children: [
              const TitleBar(),
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 18, 24, 34),
                child: Column(children: [
                  Container(
                    decoration: BoxDecoration(borderRadius: BorderRadius.circular(24), boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: .25), blurRadius: 22, offset: const Offset(0, 8))]),
                    child: ClipRRect(borderRadius: BorderRadius.circular(22), child: Image.asset('assets/logo.png', width: 92, height: 92, filterQuality: FilterQuality.medium)),
                  ),
                  const SizedBox(height: 14),
                  const Text('NQ Phone', style: TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 4),
                  const Text('ادخل بحسابك عشان تتصل وتستقبل مكالمات السنترال', textAlign: TextAlign.center, style: TextStyle(color: Colors.white70)),
                ]),
              ),
            ]),
          ),
        ),
        Expanded(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 400),
                child: Container(
                  padding: const EdgeInsets.all(22),
                  decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(22), boxShadow: softShadow(.08)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    TextField(
                      controller: _user,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: 'اسم المستخدم', prefixIcon: Icon(Icons.person_outline_rounded)),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _pass,
                      obscureText: _hide,
                      onSubmitted: (_) => _go(),
                      decoration: InputDecoration(
                        labelText: 'كلمة السر',
                        prefixIcon: const Icon(Icons.lock_outline_rounded),
                        suffixIcon: IconButton(icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined), onPressed: () => setState(() => _hide = !_hide)),
                      ),
                    ),
                    if (_err.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_err, textAlign: TextAlign.center, style: const TextStyle(color: kRed, fontWeight: FontWeight.w600))),
                    const SizedBox(height: 18),
                    Container(
                      decoration: BoxDecoration(gradient: kCallGradient, borderRadius: BorderRadius.circular(12), boxShadow: [BoxShadow(color: kCallA.withValues(alpha: .35), blurRadius: 16, offset: const Offset(0, 6))]),
                      child: FilledButton(
                        style: FilledButton.styleFrom(backgroundColor: Colors.transparent, shadowColor: Colors.transparent, minimumSize: const Size(0, 50)),
                        onPressed: _busy ? null : _go,
                        child: _busy
                            ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white))
                            : const Text('دخول', style: TextStyle(fontSize: 16)),
                      ),
                    ),
                    const SizedBox(height: 14),
                    const Text('نفس اسم المستخدم وكلمة السر بتوع تطبيق NQ Phone', textAlign: TextAlign.center, style: TextStyle(color: kMuted, fontSize: 12)),
                  ]),
                ),
              ),
            ),
          ),
        ),
      ]),
    );
  }
}
