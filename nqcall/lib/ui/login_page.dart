import 'package:flutter/material.dart';

import '../core.dart';
import '../phone.dart';
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
    final t = Theme.of(context);
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 400),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Center(
                child: Container(
                  width: 84,
                  height: 84,
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(colors: [kBrand, kBrand2]),
                    borderRadius: BorderRadius.circular(24),
                    boxShadow: [BoxShadow(color: kBrand.withValues(alpha: .35), blurRadius: 24, offset: const Offset(0, 8))],
                  ),
                  child: const Center(child: Text('NQ', style: TextStyle(color: Colors.white, fontSize: 32, fontWeight: FontWeight.w800))),
                ),
              ),
              const SizedBox(height: 18),
              Text('NQ Call', textAlign: TextAlign.center, style: t.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 4),
              Text('ادخل بحسابك عشان تتصل وتستقبل مكالمات السنترال', textAlign: TextAlign.center, style: TextStyle(color: t.hintColor)),
              const SizedBox(height: 28),
              TextField(
                controller: _user,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(labelText: 'اسم المستخدم', prefixIcon: Icon(Icons.person_outline)),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _pass,
                obscureText: _hide,
                onSubmitted: (_) => _go(),
                decoration: InputDecoration(
                  labelText: 'كلمة السر',
                  prefixIcon: const Icon(Icons.lock_outline),
                  suffixIcon: IconButton(icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined), onPressed: () => setState(() => _hide = !_hide)),
                ),
              ),
              if (_err.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_err, textAlign: TextAlign.center, style: const TextStyle(color: kRed))),
              const SizedBox(height: 20),
              FilledButton(
                onPressed: _busy ? null : _go,
                child: _busy ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5)) : const Text('دخول', style: TextStyle(fontSize: 16)),
              ),
              const SizedBox(height: 16),
              Text('نفس اسم المستخدم وكلمة السر بتوع تطبيق NQ Phone', textAlign: TextAlign.center, style: TextStyle(color: t.hintColor, fontSize: 12)),
            ]),
          ),
        ),
      ),
    );
  }
}
