#!/usr/bin/env python3
"""تشغيل سيرفر NQ على جهازك للتطوير.

  python dev/run.py              # يجهّز كل حاجة أول مرة ويشغّل السيرفر على http://localhost:8090
  python dev/run.py --password   # يغيّر باسورد دخول اللوحة المحلية
  python dev/run.py --port 9000  # بورت تاني

أول مرة بيعمل:
  .venv/          بيئة بايثون فيها مكتبات السيرفر
  .devdata/conf   إعدادات محلية (htpasswd = باسورد اللوحة المحلية)
  .devdata/data   بيانات محلية (واتساب، الأهداف، الملفات...) — منفصلة تمامًا عن السيرفر الحقيقي
  dev/.env        إعداداتك (اتعمل من dev/env.example) — مش بيترفع على جيت
"""
import argparse, base64, getpass, hashlib, os, subprocess, sys, venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
DEV = ROOT / ".devdata"
ENV_FILE = ROOT / "dev" / ".env"
WIN = os.name == "nt"
PY = VENV / ("Scripts/python.exe" if WIN else "bin/python")


def sh(*a, **kw):
    print("›", " ".join(str(x) for x in a)); subprocess.check_call([str(x) for x in a], **kw)


def ensure_venv():
    if not PY.exists():
        print("① بعمل بيئة بايثون (.venv) ...")
        venv.create(VENV, with_pip=True)
    stamp = VENV / ".req-ok"
    req = ROOT / "server" / "requirements.txt"
    if not stamp.exists() or stamp.stat().st_mtime < req.stat().st_mtime:
        print("② بنزّل مكتبات السيرفر ...")
        sh(PY, "-m", "pip", "install", "-q", "--upgrade", "pip")
        sh(PY, "-m", "pip", "install", "-q", "-r", req, "watchfiles")
        stamp.touch()


def set_password():
    conf = DEV / "conf"; conf.mkdir(parents=True, exist_ok=True)
    user = input("اسم المستخدم للوحة المحلية [admin]: ").strip() or "admin"
    while True:
        pw = getpass.getpass("باسورد اللوحة المحلية (مش بيظهر وانت بتكتب): ")
        if len(pw) >= 4 and pw == getpass.getpass("اكتبه تاني: "):
            break
        print("مش متطابقين أو قصير — حاول تاني")
    h = "{SHA}" + base64.b64encode(hashlib.sha1(pw.encode()).digest()).decode()
    (conf / "htpasswd").write_text(f"{user}:{h}\n")
    print(f"✔ اتحفظ — ادخل بـ {user}")


def load_env() -> dict:
    if not ENV_FILE.exists():
        ENV_FILE.write_text((ROOT / "dev" / "env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✔ عملت {ENV_FILE.relative_to(ROOT)} من env.example — عدّله لو عاوز تربط بداتا السنترال")
    env = {}
    for ln in ENV_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--password", action="store_true")
    a = ap.parse_args()
    ensure_venv()
    for d in ("conf", "data", "monitor"):
        (DEV / d).mkdir(parents=True, exist_ok=True)
    if a.password or not (DEV / "conf" / "htpasswd").exists():
        set_password()
    env = dict(os.environ)
    env.update(NQ_CONF=str(DEV / "conf"), NQ_DATA=str(DEV / "data"), NQ_MONITOR=str(DEV / "monitor"),
               NQ_NO_MONITOR="1", PYTHONUTF8="1")
    env.update(load_env())
    if not env.get("NQ_DB_USER"):
        print("ℹ مفيش ربط بداتا السنترال (NQ_DB_USER فاضي في dev/.env) — اللوحة هتفتح بس سجل المكالمات هيطلع خطأ.")
    print(f"\n✔ افتح: http://localhost:{a.port}/   (Ctrl+C للإيقاف — أي تعديل في server/ بيعيد التشغيل لوحده)\n")
    if not os.environ.get("NQ_NO_BROWSER"):
        import threading, webbrowser
        threading.Timer(5, lambda: webbrowser.open(f"http://localhost:{a.port}/")).start()
    os.chdir(ROOT / "server")
    sys.exit(subprocess.call([str(PY), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(a.port),
                              "--reload", "--reload-dir", str(ROOT / "server")], env=env))


if __name__ == "__main__":
    main()
