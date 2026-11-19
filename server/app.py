"""
NQ Calls — خدمة لوحة المكالمات على سيرفر السنترال
  - تقرا سجل المكالمات (CDR) من قاعدة بيانات FreePBX
  - تشغّل التسجيلات
  - تشغّل تحليل الذكاء الاصطناعي لما يتطلب (أو أسبوعيًا من cron)
بتشتغل على 127.0.0.1:8090 وApache بيوصّلها على https://<host>/calls/ ومعاها باسورد.
"""
import datetime as dt
import hmac
import json
import shutil
import time
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
CONF_DIR = Path(os.environ.get("NQ_CONF", "/etc/nq-calls"))
DATA_DIR = Path(os.environ.get("NQ_DATA", "/var/lib/nq-calls"))
ANALYSIS_DIR = DATA_DIR / "analysis"
NOTES_DIR = DATA_DIR / "notes"
SUP_FILE = DATA_DIR / "supervisor-notes.json"
SERVER_TZ = dt.datetime.now().astimezone().tzinfo   # توقيت السيرفر اللي Asterisk بيكتب بيه الـ CDR
SIM_DIR = DATA_DIR / "mobile"            # تسجيلات مكالمات الشريحة اللي التطبيق بيرفعها
SIM_INDEX = SIM_DIR / "index.jsonl"
SIM_LOCK = threading.Lock()
AUDIO_OK = {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".webm", ".aac", ".amr", ".3gp", ".opus", ".flac"}
try:
    SUP_NOTES = json.loads(SUP_FILE.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    SUP_NOTES = {}
MONITOR = Path(os.environ.get("NQ_MONITOR", "/var/spool/asterisk/monitor"))
TZ = ZoneInfo(os.environ.get("NQ_TZ", "Asia/Riyadh"))
TRUNK = "PJSIP/" + os.environ.get("NQ_TRUNK", "dinastar1")

app = FastAPI(title="NQ Calls", docs_url=None, redoc_url=None)


import files_api  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import appointments as APPT  # noqa: E402

app.include_router(files_api.router)
app.include_router(APPT.router)
import ownership as OWN  # noqa: E402
app.include_router(OWN.router)
import rep_portal as PORTAL  # noqa: E402
app.include_router(PORTAL.router)
import training as TRAIN  # noqa: E402
app.include_router(TRAIN.router)
# التطبيق (WebView) بيكلّم /app/* من origin تاني
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                   allow_headers=["*"], expose_headers=["Content-Disposition"])


# ---------------- قادة الفرق: كل قائد يشوف فريقه بس ----------------
import contextvars  # noqa: E402

SCOPE = contextvars.ContextVar("nq_scope", default=None)   # None = المدير (يشوف كله)
HTPASSWD = Path(os.environ.get("NQ_CONF", "/etc/nq-calls")) / "htpasswd"
LEADER_DENY = re.compile(r"^/api/(rubric|taxonomy|teams|logins|crm/settings|ownership/settings|ops/|wa\b|eng\b)")


def _teams_file() -> dict:
    try:
        return json.loads((DATA_DIR / "teams.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"teams": [], "assign": {}}


def leader_info(user: str) -> dict | None:
    """لو اليوزر ده قائد فريق: اسم الفريق والتحويلات اللي فيه"""
    u = (user or "").strip().lower()
    if not u:
        return None
    tm = _teams_file()
    names = {t["id"]: t["name"] for t in tm.get("teams", [])}
    m = next((m for m in tm.get("managers", []) if (m.get("user") or "").strip().lower() == u), None)
    if m:   # مدير: مجموعة فرق
        ids = [t for t in m.get("teams", []) if t in names]
        role, label = "manager", m.get("name") or user
    else:
        t = next((t for t in tm.get("teams", []) if (t.get("leaderUser") or "").strip().lower() == u), None)
        if not t:
            return None
        ids, role, label = [t["id"]], "leader", t["name"]
    exts = {e for e, tid in (tm.get("assign") or {}).items() if tid in ids}
    return {"user": user, "role": role, "teams": set(ids), "team": ids[0] if ids else "",
            "teamName": label if role == "leader" else "، ".join(names[i] for i in ids), "name": label, "exts": exts}


def in_scope(ext) -> bool:
    sc = SCOPE.get()
    return sc is None or str(ext) in sc["exts"]


def unscoped(fn, *a):
    tok = SCOPE.set(None)
    try:
        return fn(*a)
    finally:
        SCOPE.reset(tok)


# ---------------- تسجيل الدخول للوحة (جلسة بكوكي — بدل نافذة المتصفح) ----------------
import base64  # noqa: E402
import hashlib  # noqa: E402
import hmac as _hmac  # noqa: E402

SESSION_HOURS = 10            # الجلسة بتخلص بعد 10 ساعات من آخر استخدام
COOKIE = "nq_sess"
LOGIN_FAILS: dict = {}


def _session_key() -> bytes:
    f = DATA_DIR / "session.key"
    try:
        k = f.read_bytes()
        if len(k) >= 32:
            return k
    except OSError:
        pass
    k = os.urandom(48)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(k)
    try:
        f.chmod(0o600)
    except OSError:
        pass
    return k


REMEMBER_DAYS = 30            # "تذكرني": الدخول يفضل شهر


def _sign(user: str, remember: bool = False) -> str:
    life = REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600
    body = base64.urlsafe_b64encode(json.dumps({"u": user, "x": int(time.time()) + life, "r": 1 if remember else 0}).encode()).decode()
    sig = _hmac.new(_session_key(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig


def _session(tok: str) -> dict:
    try:
        body, sig = (tok or "").rsplit(".", 1)
        if not _hmac.compare_digest(sig, _hmac.new(_session_key(), body.encode(), hashlib.sha256).hexdigest()):
            return {}
        d = json.loads(base64.urlsafe_b64decode(body.encode()))
        if d.get("x", 0) <= time.time():
            return {}
        # اليوزر اتمسح أو باسورده اتغيّر بعد الدخول → الجلسة تخلص
        if d["u"] not in _ht_users():
            return {}
        return d
    except Exception:  # noqa: BLE001
        return {}


def _session_user(tok: str) -> str:
    return _session(tok).get("u", "")


def _set_session(resp, user: str, remember: bool):
    kw = dict(httponly=True, secure=True, samesite="lax", path="/")
    if remember:
        kw["max_age"] = REMEMBER_DAYS * 86400
    resp.set_cookie(COOKIE, _sign(user, remember), **kw)   # من غير "تذكرني": بتخلص لما المتصفح يتقفل


def _check_htpasswd(user: str, pw: str) -> str:
    """بيرجّع اسم اليوزر زي ما هو متسجّل لو الباسورد صح"""
    try:
        lines = HTPASSWD.read_text().splitlines()
    except OSError:
        return ""
    for ln in lines:
        if ":" not in ln:
            continue
        name, h = ln.split(":", 1)
        if name.lower() != user.lower():
            continue
        h = h.strip()
        try:
            if h.startswith(("$2y$", "$2b$", "$2a$")):
                import bcrypt
                ok = bcrypt.checkpw(pw.encode(), ("$2b$" + h[4:]).encode())
            elif h.startswith("{SHA}"):
                ok = _hmac.compare_digest(h[5:], base64.b64encode(hashlib.sha1(pw.encode()).digest()).decode())
            else:
                ok = False
        except Exception:  # noqa: BLE001
            ok = False
        return name if ok else ""
    return ""


OPEN_PATHS = ("/api/auth/", "/app/")


@app.post("/api/auth/login")
async def auth_login(request: Request):
    b = await request.json()
    user, pw = str(b.get("username") or "").strip(), str(b.get("password") or "")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    now = time.time()
    LOGIN_FAILS[ip] = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 900]
    if len(LOGIN_FAILS[ip]) >= 8:
        raise HTTPException(429, "محاولات كتير غلط — استنى ربع ساعة وجرّب تاني")
    name = _check_htpasswd(user, pw) if user and pw else ""
    if not name:
        LOGIN_FAILS[ip].append(now)
        raise HTTPException(401, "اسم المستخدم أو كلمة السر غلط")
    LOGIN_FAILS.pop(ip, None)
    resp = JSONResponse({"ok": True, "user": name})
    _set_session(resp, name, bool(b.get("remember")))
    return resp


@app.post("/api/auth/password")
async def auth_password(request: Request):
    """أي حد داخل اللوحة (المدير، مدير منطقة، قائد فريق) يغيّر باسورده"""
    user = _session_user(request.cookies.get(COOKIE, ""))
    if not user:
        raise HTTPException(401, "سجّل دخول الأول")
    b = await request.json()
    old, new = str(b.get("old") or ""), str(b.get("new") or "")
    if not _check_htpasswd(user, old):
        raise HTTPException(400, "كلمة السر الحالية غلط")
    if len(new) < 8:
        raise HTTPException(400, "كلمة السر الجديدة لازم 8 حروف على الأقل")
    if new == old:
        raise HTTPException(400, "كلمة السر الجديدة لازم تختلف عن القديمة")
    _ht_set(user, new)
    resp = JSONResponse({"ok": True})
    _set_session(resp, user, bool(_session(request.cookies.get(COOKIE, "")).get("r")))
    return resp


@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.middleware("http")
async def leader_scope(request, call_next):
    # المسار من غير /calls (السيرفر شغال بـ root-path)
    path = request.scope.get("path", "")
    rp = request.scope.get("root_path", "")
    if rp and path.startswith(rp):
        path = path[len(rp):] or "/"
    sess = _session(request.cookies.get(COOKIE, ""))
    user = sess.get("u", "")
    if path.startswith("/api/") and not path.startswith(OPEN_PATHS) and not user:
        return JSONResponse({"detail": "سجّل دخول الأول", "login": True}, 401)
    info = leader_info(user)
    if info:
        if LEADER_DENY.match(path) and (request.method != "GET" or path.startswith("/api/logins")):
            return JSONResponse({"detail": "الإعدادات للمدير بس"}, 403)
    tok = SCOPE.set(info)
    try:
        resp = await call_next(request)
    finally:
        SCOPE.reset(tok)
    if user and path.startswith("/api/") and not path.startswith("/api/auth/"):
        _set_session(resp, user, bool(sess.get("r")))
    return resp


@app.get("/api/whoami")
def whoami(request: Request):
    sc = SCOPE.get()
    if sc:
        return {"role": sc["role"], "user": sc["user"], "name": sc["name"], "team": sc["team"], "teams": sorted(sc["teams"]), "teamName": sc["teamName"]}
    return {"role": "admin", "user": _session_user(request.cookies.get(COOKIE, ""))}


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api/") and "recording" not in request.url.path:
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------- DB ----------------
def db_conf() -> dict:
    """بيانات الدخول من /etc/freepbx.conf (نفس اللي FreePBX بيستخدمها)"""
    if os.environ.get("NQ_DB_USER"):
        return dict(host=os.environ.get("NQ_DB_HOST", "localhost"), user=os.environ["NQ_DB_USER"],
                    password=os.environ.get("NQ_DB_PASS", ""))
    txt = Path("/etc/freepbx.conf").read_text()
    def get(k, default=""):
        m = re.search(rf"""\[\s*['"]{k}['"]\s*\]\s*=\s*['"]([^'"]*)['"]""", txt)
        return m.group(1) if m else default
    return dict(host=get("AMPDBHOST", "localhost"), user=get("AMPDBUSER", "freepbxuser"), password=get("AMPDBPASS"))


def query(sql: str, args: tuple, database: str | None = None) -> list:
    c = db_conf()
    con = pymysql.connect(host=c["host"], user=c["user"], password=c["password"],
                          database=database or os.environ.get("NQ_CDR_DB", "asteriskcdrdb"),
                          cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    try:
        with con.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        con.close()


def ext_from(chan: str) -> int:
    m = re.match(r"^PJSIP/(\d{2,5})-", chan or "")
    return int(m.group(1)) if m else 0


def caller_number(r: dict) -> str:
    """رقم المتصل للمكالمات الواردة: الجيتواي ساعات بيحط اسم حسابه (dinastar1) مكان الرقم
    والرقم الحقيقي في اسم العرض — ناخد أول واحد شكله رقم"""
    for v in (r.get("src"), r.get("cnum"), r.get("cnam"),
              (re.search(r'"([^"]*)"', r.get("clid") or "") or [None, None])[1]):
        v = str(v or "").strip()
        if re.fullmatch(r"\+?\d{6,}", v):
            return v
    return r.get("src") or ""


def strip_prefix(dst: str, ext: int) -> str:
    p = str(ext)
    return dst[len(p):] if len(dst) > 10 and dst.startswith(p) else dst


def stem(recordingfile: str) -> str:
    return Path(recordingfile).stem if recordingfile else ""


# ---------------- config ----------------
@app.get("/api/config")
def config_api():
    c = config()
    sc = SCOPE.get()
    if sc:
        c["reps"] = [r for r in c.get("reps", []) if str(r.get("id")) in sc["exts"]]
        c["teams"] = [t for t in c.get("teams", []) if t["id"] in sc["teams"]]
        c["leader"] = {"role": sc["role"], "teamName": sc["teamName"], "user": sc["user"]}
    return c


def config():
    f = CONF_DIR / "reps.json"
    reps = json.loads(f.read_text()) if f.exists() else {"teams": [], "reps": []}
    # أسماء الموظفين المتظبطة من شاشة الـ CRM (بتغلب على reps.json)
    try:
        over = json.loads((DATA_DIR / "rep_names.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        over = {}
    have = {str(r.get("id")) for r in reps.get("reps", [])}
    for r in reps.get("reps", []):
        if over.get(str(r.get("id"))):
            r["name"] = over[str(r.get("id"))]
    for ext, name in over.items():
        if ext not in have and name and ext.isdigit():
            reps.setdefault("reps", []).append({"id": int(ext), "name": name, "team": ""})
    try:   # رقم الخط لكل تحويلة
        lines = json.loads((DATA_DIR / "rep_lines.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        lines = {}
    try:   # تحويلات اتضافت من شاشة الموظفين (حتى من غير اسم في الـ CRM)
        extra = set(json.loads((DATA_DIR / "rep_extra.json").read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        extra = set()
    have = {str(r.get("id")) for r in reps.get("reps", [])}
    for ext in sorted((extra | set(lines)) - have, key=lambda x: int(x) if x.isdigit() else 0):
        if ext.isdigit():
            reps.setdefault("reps", []).append({"id": int(ext), "name": over.get(ext) or ext, "team": ""})
    so = sim_only()
    for ext in sorted(so - {str(r.get("id")) for r in reps.get("reps", [])}):
        if ext.isdigit():
            reps.setdefault("reps", []).append({"id": int(ext), "name": over.get(ext) or ext, "team": ""})
    for r in reps.get("reps", []):
        if lines.get(str(r.get("id"))):
            r["line"] = lines[str(r.get("id"))]
        if str(r.get("id")) in so:
            r["simOnly"] = True
    try:   # الفرق وربطها بالتحويلات من شاشة الإعدادات
        tm = json.loads((DATA_DIR / "teams.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        tm = None
    if tm:
        byid = {str(r.get("id")): r for r in reps.get("reps", [])}
        reps["teams"] = [{"id": t["id"], "name": t["name"],
                          "leader": (byid.get(str(t.get("leaderExt"))) or {}).get("name", "")} for t in tm.get("teams", [])]
        leaders = {str(t.get("leaderExt")) for t in tm.get("teams", []) if t.get("leaderExt")}
        for r in reps.get("reps", []):
            r["team"] = tm.get("assign", {}).get(str(r.get("id")), "")
            r.pop("lead", None)
            if str(r.get("id")) in leaders:
                r["lead"] = 1
    try:   # تحويلات اتمسحت من شاشة الإعدادات
        hidden = set(json.loads((DATA_DIR / "rep_hidden.json").read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        hidden = set()
    if hidden:
        reps["reps"] = [r for r in reps.get("reps", []) if str(r.get("id")) not in hidden]
    reps["aiEnabled"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return reps


APPT.CTX.update(scope=lambda: SCOPE.get(), dir=DATA_DIR,
                names=lambda: {str(r.get("id")): r.get("name", "") for r in config().get("reps", [])},
                notes=lambda a, b: load_notes(a, b))
files_api.CTX.update(dir=DATA_DIR / "files", auth=lambda e, p: check_ext_password(e, p),
                     reps=lambda: config(), host=os.environ.get("NQ_HOST", "3.89.245.93.sslip.io"))


# ---------------- CRM notes (from the phone app) ----------------
def _digits(n: str) -> str:
    return re.sub(r"\D", "", n or "")[-9:]


def load_notes(d_from: dt.date, d_to: dt.date) -> list:
    out, m = [], dt.date(d_from.year, d_from.month, 1)
    while m <= d_to:
        f = NOTES_DIR / f"{m:%Y-%m}.jsonl"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def match_note(notes: list, ext: int, number: str, t: dt.datetime):
    best, best_d = None, 15 * 60
    for n in notes:
        if str(n.get("ext")) != str(ext) or _digits(n.get("number")) != _digits(number):
            continue
        try:
            nt = dt.datetime.fromisoformat(n["started"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        d = abs((nt - t).total_seconds())
        if d < best_d:
            best, best_d = n, d
    if not best:
        return None
    return {k: best.get(k) for k in ("customer", "outcome", "summary", "followUp", "savedAt")}


FAILS: dict = {}


def sim_only() -> set:
    """موظفين بيتصلوا من شرايحهم بس (ملهمش تحويلة في السنترال) — ليهم كود من جدول الموظفين"""
    try:
        return {str(x) for x in json.loads((DATA_DIR / "sim_only.json").read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return set()


def _app_token(ext: str) -> str:
    try:
        u = json.loads((DATA_DIR / "app_users.json").read_text(encoding="utf-8")).get(str(ext)) or {}
    except Exception:  # noqa: BLE001
        u = {}
    return str(u.get("token") or "")


def check_ext_password(ext: str, password: str) -> bool:
    if not re.fullmatch(r"\d{2,6}", ext or ""):
        return False
    now = time.time()
    FAILS[ext] = [t for t in FAILS.get(ext, []) if now - t < 900]
    if len(FAILS[ext]) >= 10:
        raise HTTPException(429, "محاولات كتير، حاول بعد شوية")
    if ext in sim_only():   # موظف شريحة: بيدخل بتوكن من حساب التطبيق مش باسورد السنترال
        tok = _app_token(ext)
        ok = bool(tok) and hmac.compare_digest(tok, password or "")
        if not ok:
            FAILS[ext].append(now)
        return ok
    rows = query("SELECT data FROM sip WHERE id=%s AND keyword='secret' LIMIT 1", (ext,),
                 database=os.environ.get("NQ_PBX_DB", "asterisk"))
    ok = bool(rows) and hmac.compare_digest(str(rows[0]["data"]), password or "")
    if not ok:
        FAILS[ext].append(now)
    return ok


@app.post("/app/notes")
async def app_notes(request: Request):
    """التطبيق بيبعت ملخص المكالمة هنا (برقم التحويلة وباسوردها)"""
    hdr = {"Access-Control-Allow-Origin": "*"}
    try:
        body = json.loads(await request.body())
        ext, pw, note = str(body["ext"]), str(body["password"]), dict(body["note"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"detail": "bad request"}, 400, headers=hdr)
    if not check_ext_password(ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=hdr)
    clean = {k: str(note.get(k) or "")[:4000] for k in
             ("number", "direction", "started", "customer", "outcome", "summary", "followUp", "savedAt")}
    clean["duration"] = int(note.get("duration") or 0)
    clean["ext"] = ext
    clean["received"] = dt.datetime.now(dt.timezone.utc).isoformat()
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    with open(NOTES_DIR / f"{dt.date.today():%Y-%m}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(clean, ensure_ascii=False) + "\n")
    APPT.process_async(clean)   # لو الملاحظة فيها ميعاد → طلبات تحديد موعد
    return JSONResponse({"ok": True}, headers=hdr)


INSIGHT_PROMPT = """أنت مستشار مبيعات خبير لشركة "الجودة الوطنية لتقنية المعلومات" (NQ) في السعودية.
موظف مبيعات كتب ملخص مكالمة مع عميل. حلّل الملخص وتوقّع موقف العميل. متخمّنش حاجة مش مكتوبة.

بيانات المكالمة: {meta}
ملخص الموظف:
<summary>
{summary}
</summary>

النهارده {today}.
رجّع JSON بس (من غير أي كلام قبله أو بعده) بالشكل ده:
{{
  "temperature": "ساخن | دافئ | بارد",
  "buy_probability": 0,
  "expectation": "توقّعك لموقف العميل في جملتين بالعامية المصرية البسيطة",
  "next_action": "أهم خطوة جاية للموظف، محددة وعملية",
  "followup_date": "YYYY-MM-DD أو null",
  "risks": ["خطر أو اعتراض محتمل"],
  "suggested_message": "رسالة واتساب قصيرة مهذبة يبعتها الموظف للعميل للمتابعة"
}}"""


@app.post("/app/upload")
async def app_upload(request: Request):
    """التطبيق بيرفع تسجيل مكالمة الشريحة (الملف في الـ body والبيانات في الهيدرز)"""
    h = request.headers
    ext, pw = h.get("x-ext", ""), h.get("x-pass", "")
    if not check_ext_password(ext, pw):
        raise HTTPException(401, "رقم التحويلة أو الباسورد غلط")
    number = re.sub(r"[^\d+]", "", h.get("x-number", ""))[:20] or "unknown"
    try:
        start_ms = int(h.get("x-start", "0")); duration = int(h.get("x-duration", "0"))
    except ValueError:
        raise HTTPException(400, "بيانات غلط")
    direction = "in" if h.get("x-dir") == "in" else "out"
    suffix = Path(h.get("x-filename", "rec.m4a")).suffix.lower()
    if suffix not in AUDIO_OK:
        raise HTTPException(400, "نوع الملف مش صوت")
    cid = f"sim-{ext}-{start_ms}"
    if cid in load_sim_calls():
        return {"ok": True, "id": cid, "dup": True}
    body = await request.body()
    if not body or len(body) > 60 * 1024 * 1024:
        raise HTTPException(413, "الملف فاضي أو كبير جدًا")
    t = dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc)
    rel = Path(f"{t:%Y}/{t:%m}") / f"{direction}-{number}-{ext}-{t:%Y%m%d}-{t:%H%M%S}-sim{start_ms}{suffix}"
    dest = SIM_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    if suffix in {".amr", ".3gp"} and shutil.which("ffmpeg"):  # صيغ مش كل خدمات التحويل بتقبلها
        mp3 = dest.with_suffix(".mp3")
        if subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dest), str(mp3)]).returncode == 0:
            dest.unlink(missing_ok=True); dest = mp3; rel = rel.with_suffix(".mp3")
    rec = dict(id=cid, ext=ext, number=number, start=start_ms, duration=duration, dir=direction,
               file=str(rel), uploaded=dt.datetime.now(TZ).isoformat())
    with SIM_LOCK, open(SIM_INDEX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "id": cid}


@app.post("/app/insight")
async def app_insight(request: Request):
    """تحليل سريع لملخص مكالمة واحدة (التطبيق بيطلبه بعد المكالمة)"""
    hdr = {"Access-Control-Allow-Origin": "*"}
    try:
        body = json.loads(await request.body())
        ext, pw, note = str(body["ext"]), str(body["password"]), dict(body["note"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"detail": "bad request"}, 400, headers=hdr)
    if not check_ext_password(ext, pw):
        return JSONResponse({"detail": "رقم التحويلة أو الباسورد غلط"}, 401, headers=hdr)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"detail": "مفتاح Claude API مش متضاف على السيرفر"}, 503, headers=hdr)
    summary = str(note.get("summary") or "")[:4000].strip()
    if not summary:
        return JSONResponse({"detail": "الملخص فاضي"}, 400, headers=hdr)
    meta = {k: note.get(k) for k in ("customer", "outcome", "direction", "duration")}
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=os.environ.get("NQ_CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=3000,
            messages=[{"role": "user", "content": INSIGHT_PROMPT.format(
                meta=json.dumps(meta, ensure_ascii=False), summary=summary, today=dt.date.today().isoformat())}])
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        out = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001
        m = str(e)
        if "authentication_error" in m or "x-api-key" in m:
            m = "مفتاح Claude API على السيرفر غلط — المدير يكتب على السيرفر: sudo nq-setkey"
        elif "credit balance" in m:
            m = "رصيد حساب Anthropic خلص"
        return JSONResponse({"detail": f"فشل التحليل: {m}"}, 502, headers=hdr)
    return JSONResponse(out, headers=hdr)


@app.get("/api/notes")
def notes(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    return load_notes(date_from, date_to)


# ---------------- calls ----------------
@app.get("/api/calls")
def calls(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    # الـ CDR بيتسجّل بتوقيت السيرفر المحلي (مش UTC)
    start_a = dt.datetime.combine(date_from, dt.time.min, TZ)
    end_a = dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min, TZ)
    start = start_a.astimezone(SERVER_TZ).replace(tzinfo=None)
    end = end_a.astimezone(SERVER_TZ).replace(tzinfo=None)
    rows = query(
        """SELECT uniqueid, linkedid, calldate, src, cnum, cnam, clid, dst, channel, dstchannel,
                  disposition, duration, billsec, recordingfile
           FROM cdr
           WHERE calldate >= %s AND calldate < %s
             AND (channel LIKE %s OR dstchannel LIKE %s)
           ORDER BY calldate""",
        (start, end, TRUNK + "%", TRUNK + "%"))

    groups: dict = {}
    for r in rows:
        groups.setdefault(r["linkedid"] or r["uniqueid"], []).append(r)

    notes_all = load_notes(date_from - dt.timedelta(days=1), date_to + dt.timedelta(days=1))
    out = []
    for g in groups.values():
        main = next((r for r in g if r["disposition"] == "ANSWERED"), g[-1])
        inbound = (main["channel"] or "").startswith(TRUNK)
        ext = ext_from(main["dstchannel"]) if inbound else ext_from(main["channel"])
        if not ext:
            continue
        status = {"ANSWERED": "تم الرد", "BUSY": "مرفوضة"}.get(main["disposition"], "فائتة" if inbound else "لم يرد")
        rec = next((r["recordingfile"] for r in g if r["recordingfile"]), "")
        t = main["calldate"].replace(tzinfo=SERVER_TZ).astimezone(dt.timezone.utc)
        out.append(dict(
            id=main["uniqueid"], time=t.isoformat(), ext=ext, dir="in" if inbound else "out",
            status=status, talkSec=int(main["billsec"] or 0),
            ringSec=max(0, int(main["duration"] or 0) - int(main["billsec"] or 0)),
            number=caller_number(main) if inbound else strip_prefix(main["dst"] or "", ext),
            customer=None,
            note=match_note(notes_all, ext, caller_number(main) if inbound else strip_prefix(main["dst"] or "", ext), t),
            hasRecording=bool(rec) and main["disposition"] == "ANSWERED",
            analyzed=bool(rec) and (ANALYSIS_DIR / (stem(rec) + ".json")).exists(),
            rec=stem(rec) if rec else "",
            transcribed=bool(rec) and ((ANALYSIS_DIR / (stem(rec) + ".tr.json")).exists()
                                       or (ANALYSIS_DIR / (stem(rec) + ".json")).exists()),
            supNote=(SUP_NOTES.get(main["uniqueid"]) or {}).get("text", ""),
        ))
    # مكالمات الشريحة اللي اترفعت من التطبيق
    for r in load_sim_calls().values():
        t = dt.datetime.fromtimestamp(r["start"] / 1000, dt.timezone.utc)
        if not (start_a <= t < end_a):
            continue
        st = Path(r["file"]).stem
        out.append(dict(
            id=r["id"], time=t.isoformat(), ext=int(r["ext"]), dir=r["dir"], status="تم الرد",
            talkSec=int(r.get("duration") or 0), ringSec=0, number=r["number"], customer=None,
            note=match_note(notes_all, int(r["ext"]), r["number"], t), source="sim",
            hasRecording=True, rec=st,
            analyzed=(ANALYSIS_DIR / (st + ".json")).exists(),
            transcribed=(ANALYSIS_DIR / (st + ".tr.json")).exists() or (ANALYSIS_DIR / (st + ".json")).exists(),
            supNote=(SUP_NOTES.get(r["id"]) or {}).get("text", ""),
        ))
    out.sort(key=lambda c: c["time"], reverse=True)
    if SCOPE.get():
        out = [c for c in out if in_scope(c["ext"])]
    return out


def load_sim_calls() -> dict:
    out = {}
    try:
        with open(SIM_INDEX, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    out[r["id"]] = r
                except Exception:  # noqa: BLE001
                    pass
    except FileNotFoundError:
        pass
    return out


def rec_file(call_id: str) -> Path:
    """مسار ملف التسجيل لأي مكالمة — سنترال أو شريحة"""
    if call_id.startswith("sim-"):
        r = load_sim_calls().get(call_id)
        p = SIM_DIR / r["file"] if r else None
        if not p or not p.exists():
            raise HTTPException(404, "ملف التسجيل مش موجود على السيرفر")
        return p
    return _recording_path(_recording_row(call_id))


def _recording_row(call_id: str) -> dict:
    rows = query("SELECT calldate, recordingfile FROM cdr WHERE (uniqueid=%s OR linkedid=%s) AND recordingfile<>'' LIMIT 1",
                 (call_id, call_id))
    if not rows:
        raise HTTPException(404, "مفيش تسجيل للمكالمة دي")
    return rows[0]


def _recording_path(row: dict) -> Path:
    name = Path(row["recordingfile"]).name  # يمنع ../
    d = row["calldate"]
    p = MONITOR / f"{d:%Y}" / f"{d:%m}" / f"{d:%d}" / name
    if not p.exists():
        hits = list(MONITOR.glob(f"**/{name}"))
        if not hits:
            raise HTTPException(404, "ملف التسجيل مش موجود على السيرفر")
        p = hits[0]
    return p


@app.get("/api/recording/{call_id}")
def recording(call_id: str, download: int = 0):
    p = rec_file(call_id)
    mt = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
          ".webm": "audio/webm", ".aac": "audio/aac", ".amr": "audio/amr", ".3gp": "audio/3gpp", ".flac": "audio/flac"}
    return FileResponse(p, media_type=mt.get(p.suffix.lower(), "audio/wav"), filename=p.name if download else None)


# ---------------- AI analysis ----------------
JOB = {"running": False, "started": None, "finished": None, "log": "", "range": None}
JOB_LOCK = threading.Lock()


def _run_job(args: list):
    cmd = ["nice", "-n", "19", sys.executable, str(HERE / "analyzer.py"), *args]
    env = dict(os.environ, NQ_AI_OUT=str(ANALYSIS_DIR))
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)
        JOB["log"] = (p.stdout + p.stderr)[-4000:]
    except Exception as e:  # noqa: BLE001
        JOB["log"] = f"فشل التحليل: {e}"
    finally:
        JOB["running"] = False
        JOB["finished"] = dt.datetime.now(TZ).isoformat()


@app.post("/api/analyze")
async def analyze(request: Request, date_from: dt.date | None = Query(None, alias="from"),
                  date_to: dt.date | None = Query(None, alias="to")):
    """يحلّل مكالمات محددة (JSON: {"ids": [...]}) أو كل مكالمات الفترة"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "مفتاح Claude API مش متضاف على السيرفر")
    import importlib.util
    if not os.environ.get("OPENAI_API_KEY") and not importlib.util.find_spec("faster_whisper"):
        raise HTTPException(400, "تحليل التسجيلات (المرحلة 2) لسه مش متركّب على السيرفر")
    ids, body = [], None
    try:
        body = await request.json()
        ids = [str(i) for i in (body or {}).get("ids", [])][:500]
    except Exception:  # noqa: BLE001
        pass
    if ids:
        files = []
        for i in ids:
            try:
                files.append(str(rec_file(i)))
            except HTTPException:
                pass
        if not files:
            raise HTTPException(404, "المكالمات المختارة مفيهاش تسجيلات على السيرفر")
        mode = (body or {}).get("mode") if isinstance(body, dict) else None
        if mode == "transcript":
            args, rng = ["--transcript-only", "--redo", "--min-sec", "0", *files], [f"تحويل {len(files)} مكالمة لنص", ""]
        else:
            args, rng = ["--redo", "--min-sec", "0", *files], [f"{len(files)} مكالمة", ""]
    elif date_from and date_to:
        args, rng = ["--from", str(date_from), "--to", str(date_to), "--min-sec", "10"], [str(date_from), str(date_to)]
    else:
        raise HTTPException(400, "اختار مكالمات أو فترة")
    with JOB_LOCK:
        if JOB["running"]:
            raise HTTPException(409, "في تحليل شغّال بالفعل")
        JOB.update(running=True, started=dt.datetime.now(TZ).isoformat(), finished=None, log="", range=rng)
        (ANALYSIS_DIR / "progress.json").unlink(missing_ok=True)
    threading.Thread(target=_run_job, args=(args,), daemon=True).start()
    return JOB


@app.get("/api/analyze/status")
def analyze_status():
    prog = None
    try:
        prog = json.loads((ANALYSIS_DIR / "progress.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {**JOB, "progress": prog}


@app.get("/api/analysis")
def analysis(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    """نتايج التحليل للمكالمات في الفترة، مربوطة بالمكالمة"""
    out = []
    for c in calls(date_from, date_to):
        if not c["analyzed"]:
            continue
        f = ANALYSIS_DIR / (c["rec"] + ".json")
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append(dict(call=c, analysis=r.get("analysis", {}),
                        transcript=r.get("transcript", {}).get("lines", [])))
    return out


@app.get("/api/call/{call_id}")
def call_detail(call_id: str):
    """النص والتحليل وملاحظة المدير لمكالمة واحدة"""
    out = {"transcript": [], "analysis": None, "supNote": (SUP_NOTES.get(call_id) or {}).get("text", "")}
    try:
        st = rec_file(call_id).stem
    except HTTPException:
        return out
    for f in (ANALYSIS_DIR / (st + ".json"), ANALYSIS_DIR / (st + ".tr.json")):
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if "analysis" in d:
                out["analysis"] = d["analysis"]
                out["transcript"] = d.get("transcript", {}).get("lines", [])
                break
            out["transcript"] = d.get("lines", [])
    return out


SUP_LOCK = threading.Lock()


@app.post("/api/supnote")
async def save_supnote(request: Request):
    """ملاحظة المدير على مكالمة"""
    body = await request.json()
    cid, text = str(body.get("id", ""))[:64], str(body.get("text", ""))[:4000]
    if not cid:
        raise HTTPException(400, "id مطلوب")
    with SUP_LOCK:
        if text.strip():
            SUP_NOTES[cid] = {"text": text.strip(), "at": dt.datetime.now(TZ).isoformat()}
        else:
            SUP_NOTES.pop(cid, None)
        tmp = SUP_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(SUP_NOTES, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SUP_FILE)
    return {"ok": True}


# ---------------- بنود التقييم ----------------
import rubric as RUBRIC  # noqa: E402


@app.get("/api/rubric")
def get_rubric():
    return {"items": RUBRIC.load(), "default": RUBRIC.DEFAULT}


@app.put("/api/rubric")
async def put_rubric(request: Request):
    try:
        items = (await request.json()).get("items")
        return {"items": RUBRIC.save(items)}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- مشاكل العملاء + أخطاء الموظفين + أسباب عدم الشراء ----------------
import taxonomy as TAX  # noqa: E402


@app.get("/api/taxonomy")
def get_taxonomy():
    return {"items": TAX.load(), "default": TAX.DEFAULT, "labels": TAX.LABELS}


@app.put("/api/taxonomy")
async def put_taxonomy(request: Request):
    try:
        return {"items": TAX.save((await request.json()).get("items") or {})}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/insights")
def insights(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    rows = analysis(date_from, date_to)
    names = {r.get("id"): r.get("name", "") for r in config().get("reps", [])}
    tax = TAX.load()
    nb, pains, reps = {}, {}, {}
    classified = 0
    for x in rows:
        a, c = x["analysis"], x["call"]
        if not any(k in a for k in ("customer_pains", "agent_issues", "no_buy_reason")):
            continue
        classified += 1
        ex = {"id": c["id"], "time": c["time"], "number": c["number"], "rep": names.get(c["ext"], str(c["ext"])),
              "lesson": a.get("lesson") or ""}
        r = a.get("no_buy_reason")
        if isinstance(r, dict) and r.get("type"):
            e = nb.setdefault(r["type"], {"type": r["type"], "count": 0, "examples": []})
            e["count"] += 1
            e["examples"].append({**ex, "detail": r.get("detail", "")})
        for p in a.get("customer_pains") or []:
            e = pains.setdefault(p["type"], {"type": p["type"], "count": 0, "examples": []})
            e["count"] += 1
            e["examples"].append({**ex, "detail": p.get("detail", "")})
        rp = reps.setdefault(c["ext"], {"ext": c["ext"], "name": names.get(c["ext"], str(c["ext"])), "calls": 0, "issues": {}, "examples": {}})
        rp["calls"] += 1
        for i in a.get("agent_issues") or []:
            rp["issues"][i["type"]] = rp["issues"].get(i["type"], 0) + 1
            rp["examples"].setdefault(i["type"], []).append({**ex, "detail": i.get("detail", "")})
    tot_nb = sum(v["count"] for v in nb.values()) or 1
    for v in nb.values():
        v["pct"] = round(v["count"] * 100 / tot_nb)
        v["examples"] = sorted(v["examples"], key=lambda e: e["time"], reverse=True)[:8]
    for v in pains.values():
        v["examples"] = sorted(v["examples"], key=lambda e: e["time"], reverse=True)[:8]
    for v in reps.values():
        v["top"] = max(v["issues"], key=v["issues"].get) if v["issues"] else None
        v["examples"] = {k: e[:5] for k, e in v["examples"].items()}
    return {"analyzed": len(rows), "classified": classified, "taxonomy": tax,
            "no_buy": sorted(nb.values(), key=lambda v: -v["count"]),
            "pains": sorted(pains.values(), key=lambda v: -v["count"]),
            "reps": sorted(reps.values(), key=lambda v: -sum(v["issues"].values())),
            "lessons": _lessons_cache().get(f"{date_from}|{date_to}")}


LESSONS_FILE = DATA_DIR / "lessons.json"
LESSONS_PROMPT = """إنت مدير مبيعات خبير لشركة "الجودة الوطنية لتقنية المعلومات" (NQ) — برامج محاسبة وإدارة في السعودية.
دي خلاصة تحليل مكالمات التيلي سيلز في الفترة من {d1} لـ {d2}: أسباب عدم الشراء بعددها وأمثلة، ومشاكل العملاء، وأخطاء كل موظف، والدروس من كل مكالمة.
<data>
{data}
</data>

طلّع الدروس المستفادة. رجّع JSON بس:
{{
  "overall": ["أهم 5 دروس للفريق كله — عملية ومحددة"],
  "by_reason": [{{"type": "سبب عدم الشراء", "insight": "ليه بيحصل", "what_to_do": "إيه اللي المندوب يعمله", "script": "جملة يقولها المندوب بالعامية لما العميل يقول السبب ده"}}],
  "by_rep": [{{"ext": 100, "focus": "أهم حاجة الموظف ده يشتغل عليها", "training": "تمرين أو تدريب مقترح"}}],
  "opportunities": ["فرص بيع أو منتجات/خدمات العملاء محتاجينها من المشاكل اللي اتقالت"]
}}"""


LESSONS_TOOL = {
    "name": "save_lessons",
    "description": "احفظ الدروس المستفادة من تحليل المكالمات",
    "input_schema": {
        "type": "object",
        "properties": {
            "overall": {"type": "array", "items": {"type": "string"}, "description": "أهم 5 دروس للفريق كله"},
            "by_reason": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"type": "string"}, "insight": {"type": "string"},
                "what_to_do": {"type": "string"}, "script": {"type": "string"}},
                "required": ["type", "insight", "what_to_do", "script"]}},
            "by_rep": {"type": "array", "items": {"type": "object", "properties": {
                "ext": {"type": "integer"}, "focus": {"type": "string"}, "training": {"type": "string"}},
                "required": ["ext", "focus", "training"]}},
            "opportunities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["overall", "by_reason", "by_rep", "opportunities"],
    },
}


def _loose_json(raw: str):
    """يحاول يقرا JSON حتى لو جواه ```json أو فواصل زيادة"""
    raw = re.sub(r"```(?:json)?", "", raw or "")
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b <= a:
        return None
    txt = raw[a:b + 1]
    for t in (txt, re.sub(r",\s*([}\]])", r"\1", txt)):
        try:
            return json.loads(t)
        except ValueError:
            pass
    return None


def _lessons_cache() -> dict:
    try:
        return json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


@app.post("/api/insights/lessons")
def make_lessons(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "مفتاح Claude API مش متضاف على السيرفر")
    ins = insights(date_from, date_to)
    if not ins["classified"]:
        raise HTTPException(400, "مفيش مكالمات متحلّلة بالأنواع في الفترة دي — حلّل مكالمات الأول")
    slim = {
        "no_buy": [{"type": v["type"], "count": v["count"], "examples": [e["detail"] for e in v["examples"][:5]]} for v in ins["no_buy"]],
        "customer_pains": [{"type": v["type"], "count": v["count"], "examples": [e["detail"] for e in v["examples"][:4]]} for v in ins["pains"]],
        "reps": [{"ext": v["ext"], "name": v["name"], "calls": v["calls"], "issues": v["issues"]} for v in ins["reps"]],
        "call_lessons": [e["lesson"] for v in ins["no_buy"] for e in v["examples"] if e.get("lesson")][:40],
    }
    import anthropic
    client = anthropic.Anthropic()
    prompt = LESSONS_PROMPT.format(d1=date_from, d2=date_to, data=json.dumps(slim, ensure_ascii=False, indent=1))
    prompt += "\n\nرجّع النتيجة عن طريق أداة save_lessons."
    kw = dict(model=os.environ.get("NQ_CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=16000,
              messages=[{"role": "user", "content": prompt}], tools=[LESSONS_TOOL])
    try:   # الأداة بتضمن إن الرد JSON سليم
        msg = client.messages.create(tool_choice={"type": "tool", "name": "save_lessons"}, **kw)
    except anthropic.BadRequestError:
        msg = client.messages.create(tool_choice={"type": "auto"}, **kw)
    res = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if not isinstance(res, dict):
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        res = _loose_json(raw)
    if not isinstance(res, dict):
        why = " (الرد اتقطع لأنه طويل)" if getattr(msg, "stop_reason", "") == "max_tokens" else ""
        raise HTTPException(502, "رد Claude مش مفهوم" + why + " — جرّب تاني")
    for k in ("overall", "by_reason", "by_rep", "opportunities"):
        res.setdefault(k, [])
    res["generated"] = dt.datetime.now(TZ).isoformat()
    res["calls"] = ins["classified"]
    cache = _lessons_cache()
    cache[f"{date_from}|{date_to}"] = res
    LESSONS_FILE.write_text(json.dumps(dict(list(cache.items())[-30:]), ensure_ascii=False), encoding="utf-8")
    return res


# ---------------- page ----------------
@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.exception_handler(pymysql.MySQLError)
def db_error(_, exc):
    return JSONResponse({"detail": f"خطأ في قاعدة بيانات السنترال: {exc}"}, status_code=500)


@app.get("/api/teams")
def get_teams():
    c = config()
    users = _ht_users()
    raw = {t["id"]: t for t in _teams_file().get("teams", [])}
    return {"teams": [{"id": t["id"], "name": t["name"],
                       "leaderExt": next((str(r["id"]) for r in c["reps"] if r.get("team") == t["id"] and r.get("lead")), ""),
                       "leaderUser": raw.get(t["id"], {}).get("leaderUser", ""),
                       "hasPassword": raw.get(t["id"], {}).get("leaderUser", "") in users}
                      for t in c.get("teams", [])],
            "reps": [{"ext": str(r["id"]), "name": r.get("name", ""), "team": r.get("team", "")} for r in c.get("reps", [])],
            "managers": [{"name": m.get("name", ""), "user": m.get("user", ""), "teams": m.get("teams", []),
                          "hasPassword": m.get("user", "") in users} for m in _teams_file().get("managers", [])]}


@app.put("/api/teams")
async def put_teams(request: Request):
    b = await request.json()
    teams, seen = [], set()
    for t in b.get("teams") or []:
        name = str(t.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(400, "في فريق من غير اسم")
        tid = re.sub(r"[^\w-]", "", str(t.get("id") or "")) or ("t" + str(len(teams) + 1))
        while tid in seen:
            tid += "_"
        seen.add(tid)
        old = next((o for o in _teams_file().get("teams", []) if o.get("id") == tid), {})
        lu = str(t["leaderUser"]).strip() if "leaderUser" in t else old.get("leaderUser", "")
        if lu and not re.fullmatch(r"[\w.\-@]{3,40}", lu):
            raise HTTPException(400, f"يوزر قائد \"{name}\": من 3 لـ 40 حرف من غير مسافات")
        teams.append({"id": tid, "name": name, "leaderExt": str(t.get("leaderExt") or ""), "leaderUser": lu,
                      "_pw": str(t.get("leaderPassword") or "")})
    # اليوزرات: مايتكرروش، ومايبقوش يوزر المدير
    old_leaders = {(o.get("leaderUser") or "").lower() for o in _teams_file().get("teams", []) if o.get("leaderUser")}
    admins = {u.lower() for u in _ht_users()} - old_leaders
    seen_u = set()
    for t in teams:
        k = t["leaderUser"].lower()
        if not k:
            continue
        if k in seen_u:
            raise HTTPException(400, f"يوزر القائد \"{t['leaderUser']}\" متكرر")
        if k in admins:
            raise HTTPException(400, f"\"{t['leaderUser']}\" ده يوزر المدير — اختار اسم تاني")
        seen_u.add(k)
        if len(t["_pw"]) and len(t["_pw"]) < 8:
            raise HTTPException(400, f"باسورد قائد \"{t['name']}\" لازم 8 حروف على الأقل")
        if not t["_pw"] and t["leaderUser"] not in _ht_users():
            raise HTTPException(400, f"اكتب باسورد لقائد \"{t['name']}\"")
    # المديرين: نتأكد من كل حاجة قبل ما نكتب أي باسورد (يا كله يتحفظ يا ولا حاجة)
    managers, mgr_pw, mgr_removed = _check_managers(b, seen, seen_u)
    for t in teams:
        if t["leaderUser"] and t["_pw"]:
            _ht_set(t["leaderUser"], t["_pw"])
        t.pop("_pw", None)
    for u, pw in mgr_pw:
        _ht_set(u, pw)
    keep = seen_u | {m["user"].lower() for m in managers}
    for u in (old_leaders | mgr_removed) - keep:   # قائد أو مدير اتشال → يتقفل دخوله
        real = next((x for x in _ht_users() if x.lower() == u), None)
        if real:
            _ht_del(real)
    assign = {str(k): str(v) for k, v in (b.get("assign") or {}).items() if str(k).isdigit() and str(v) in seen}
    f = DATA_DIR / "teams.json"
    f.write_text(json.dumps({"teams": teams, "assign": assign, "managers": managers}, ensure_ascii=False, indent=1), encoding="utf-8")
    return get_teams()


def _check_managers(b: dict, team_ids: set, leader_users: set):
    """المديرين: كل مدير ليه يوزر وباسورد ومجموعة فرق يشوفها. لو الطلب مفيهوش managers بيفضلوا زي ما هم.
    بيرجّع (القايمة، الباسوردات اللي هتتكتب، اليوزرات اللي اتشالت) — من غير ما يكتب حاجة"""
    old = _teams_file().get("managers", [])
    if "managers" not in b:
        return [{**m, "teams": [t for t in m.get("teams", []) if t in team_ids]} for m in old], [], set()
    old_users = {(m.get("user") or "").lower(): m for m in old}
    admins = {u.lower() for u in _ht_users()} - set(old_users) - {(t.get("leaderUser") or "").lower() for t in _teams_file().get("teams", [])}
    out, seen, pws = [], set(), []
    for m in b.get("managers") or []:
        name = str(m.get("name") or "").strip()[:60]
        user = str(m.get("user") or "").strip()
        pw = str(m.get("password") or "")
        if not (name or user):
            continue
        if not name or not user:
            raise HTTPException(400, "كل مدير لازم يبقى ليه اسم ويوزر")
        if not re.fullmatch(r"[\w.\-@]{3,40}", user):
            raise HTTPException(400, f"يوزر المدير \"{name}\": من 3 لـ 40 حرف من غير مسافات")
        k = user.lower()
        if k in seen or k in leader_users:
            raise HTTPException(400, f"اليوزر \"{user}\" مستخدم لحد تاني")
        if k in admins:
            raise HTTPException(400, f"\"{user}\" ده يوزر المدير العام — اختار اسم تاني")
        if pw and len(pw) < 8:
            raise HTTPException(400, f"باسورد المدير \"{name}\" لازم 8 حروف على الأقل")
        if not pw and user not in _ht_users():
            raise HTTPException(400, f"اكتب باسورد للمدير \"{name}\"")
        ids = [t for t in (m.get("teams") or []) if t in team_ids]
        if not ids:
            raise HTTPException(400, f"اختار فريق واحد على الأقل للمدير \"{name}\"")
        seen.add(k)
        if pw:
            pws.append((user, pw))
        out.append({"name": name, "user": user, "teams": ids})
    return out, pws, set(old_users) - seen


def _ht_users() -> set:
    try:
        return {ln.split(":", 1)[0] for ln in HTPASSWD.read_text().splitlines() if ":" in ln}
    except Exception:  # noqa: BLE001
        return set()


def _ht_set(user: str, pw: str):
    r = subprocess.run(["htpasswd", "-iB", str(HTPASSWD), user], input=pw, text=True, capture_output=True)
    if r.returncode != 0:
        raise HTTPException(500, "مقدرتش أحفظ باسورد القائد: " + (r.stderr or "").strip()[:200]
                            + " — شغّل sudo nq-update الأول")


def _ht_del(user: str):
    subprocess.run(["htpasswd", "-D", str(HTPASSWD), user], capture_output=True, text=True)


def _analysis_of(rec: str):
    if not rec:
        return None
    try:
        return json.loads((ANALYSIS_DIR / (rec + ".json")).read_text(encoding="utf-8")).get("analysis")
    except Exception:  # noqa: BLE001
        return None


OWN.CTX.update(dir=DATA_DIR, calls=lambda a, b: unscoped(calls, a, b), scope=lambda: SCOPE.get(), auth=lambda e, p: check_ext_password(e, p),
               names=lambda: {str(r.get("id")): r.get("name", "") for r in config().get("reps", [])},
               analysis=_analysis_of,
               crm=lambda n: APPT.match_client(n) if APPT.crm_configured() else None)


def _sip_secret(ext: str) -> str:
    rows = query("SELECT data FROM sip WHERE id=%s AND keyword='secret' LIMIT 1", (ext,),
                 database=os.environ.get("NQ_PBX_DB", "asterisk"))
    return str(rows[0]["data"]) if rows else ""


PORTAL.CTX.update(dir=DATA_DIR, sip_secret=_sip_secret, check_ext=lambda e, p: check_ext_password(e, p),
                  names=lambda: {str(r.get("id")): r.get("name", "") for r in config().get("reps", [])},
                  analysis=lambda a, b: analysis(a, b), calls=lambda a, b: calls(a, b),
                  rubric=lambda: RUBRIC.load(), lessons=lambda: _lessons_cache(),
                  appointments=lambda: APPT._load(), conflicts=lambda: OWN._jload("conflicts.json", []), tz=TZ)
PORTAL.CTX.update(training=lambda ext: TRAIN.latest_for_ext(ext))
PORTAL.CTX.update(sim_only=sim_only)
import wainbox as WAI  # noqa: E402
app.include_router(WAI.router)


def _last_caller(number: str):
    d = _digits(number)
    today = dt.datetime.now(TZ).date()
    hit = [c for c in OPS.calls_c(today - dt.timedelta(days=60), today) if _digits(c.get("number")) == d]
    return str(max(hit, key=lambda c: c["time"])["ext"]) if hit else None


WAI.CTX.update(dir=DATA_DIR, check_ext=lambda e, p: check_ext_password(e, p), scope=lambda: SCOPE.get(),
               names=lambda: {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])},
               owner_of=lambda n: OWN.owner_of(n), last_caller=_last_caller)
import ops as OPS  # noqa: E402
app.include_router(OPS.router)
OPS.CTX.update(dir=DATA_DIR, tz=TZ, trunk=TRUNK.split("/", 1)[1], query=query, scope=lambda: SCOPE.get(),
               calls=lambda a, b: unscoped(calls, a, b), notes=load_notes, teams=_teams_file,
               analysis=lambda a, b: unscoped(analysis, a, b),
               names=lambda: {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])},
               reps=lambda: unscoped(config).get("reps", []),
               lines=lambda: {str(r.get("id")): r.get("line", "") for r in unscoped(config).get("reps", []) if r.get("line")},
               owner_of=lambda n: OWN.owner_of(n), check_ext=lambda e, p: check_ext_password(e, p),
               appointments=lambda: APPT._load(), training=lambda: TRAIN.load_all(),
               crm_get=lambda path, params: APPT._items(APPT._get_json(path, params)), crm_on=APPT.crm_configured,
               sales_ids=lambda: APPT.crm_settings()["salesIds"])
OPS.start()
import targets as TGT  # noqa: E402
app.include_router(TGT.router)
def _lookup_extra(number: str) -> dict:
    """بطاقة العميل: تارجت مفتوح + آخر ميعاد"""
    d, out = _digits(number), {}
    today = str(dt.datetime.now(TZ).date())
    tg = [x for x in TGT.load() if _digits(x["number"]) == d and x["date"] >= str(dt.datetime.now(TZ).date() - dt.timedelta(days=7))]
    if tg:
        x = sorted(tg, key=lambda x: abs((dt.date.fromisoformat(x["date"]) - dt.date.fromisoformat(today)).days))[0]
        nm = {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])}
        out["target"] = {"date": x["date"], "name": nm.get(x["ext"], x["ext"]), "note": x.get("note", "")}
    w = WA.log_for(number)
    if w:
        out["wa"] = {"at": w["at"], "title": w.get("title", ""), "ext": w.get("ext", "")}
    ap = [a for a in APPT._load() if _digits(a.get("number")) == d and a.get("status") != "rejected"]
    if ap:
        a = sorted(ap, key=lambda a: a.get("created") or "")[-1]
        out["appt"] = {"date": a.get("date"), "time": a.get("time"), "status": a.get("status"), "by": a.get("repName", "")}
    return out


import wa as WA  # noqa: E402
app.include_router(WA.router)
WA.CTX.update(dir=DATA_DIR, host=os.environ.get("NQ_HOST", "3.89.245.93.sslip.io"), check_ext=lambda e, p: check_ext_password(e, p),
              names=lambda: {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])})
OWN.CTX.update(extra=_lookup_extra)


_PC: dict = {}


@app.get("/app/partner-check")
def partner_check(request: Request, phone: str = ""):
    """لبوابة الشركاء (nqcustomers): الرقم ده مسجّل عندنا؟ في الـ CRM أو اتكلّم من السنترال — ومين المندوب"""
    key = os.environ.get("NQ_PARTNER_KEY", "")
    if not key or not hmac.compare_digest(request.headers.get("x-partner-key", ""), key):
        raise HTTPException(401, "unauthorized")
    d = _digits(phone)
    if len(d) < 7:
        return {"found": False, "invalid": True}
    hit = _PC.get(d)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    out: dict = {"found": False}
    try:
        c = APPT.crm_full(d)
    except Exception as e:  # noqa: BLE001
        c, out["crmError"] = None, str(e)[:120]
    if c:
        out.update(found=True, source="crm", crm=c)
    names = {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])}
    own = OWN.owner_of(d) or {}
    today = dt.datetime.now(TZ).date()
    hist = [x for x in unscoped(calls, today - dt.timedelta(days=180), today) if _digits(x.get("number")) == d]
    if hist:
        last = max(hist, key=lambda x: x["time"])
        talked = [x for x in hist if x.get("status") == "تم الرد"]
        lt = max(talked, key=lambda x: x["time"]) if talked else last
        days = (today - dt.datetime.fromisoformat(lt["time"].replace("Z", "+00:00")).astimezone(TZ).date()).days
        note = next((x.get("note") for x in sorted(hist, key=lambda x: x["time"], reverse=True) if x.get("note")), None) or {}
        out["calls"] = {"count": len(hist), "answered": len(talked), "lastContact": lt["time"], "days": days,
                        "rep": names.get(str(lt["ext"]), str(lt["ext"])), "customer": note.get("customer") or ""}
        out["found"] = True
        out.setdefault("source", "calls")
    if own.get("ext"):
        out["owner"] = names.get(str(own["ext"]), str(own["ext"]))
    out["rep"] = (c or {}).get("rep") or out.get("owner") or (out.get("calls") or {}).get("rep") or ""
    out["company"] = (c or {}).get("company") or (out.get("calls") or {}).get("customer") or ""
    _PC[d] = (time.time(), out)
    if len(_PC) > 500:
        _PC.clear()
    return out
import eng as ENG  # noqa: E402
app.include_router(ENG.router)
ENG.CTX.update(dir=DATA_DIR, tz=TZ, check_ext=lambda e, p: check_ext_password(e, p), get_json=APPT._get_json, items=APPT._items)
TGT.CTX.update(dir=DATA_DIR, tz=TZ, calls=lambda a, b: OPS.calls_c(a, b), scope=lambda: SCOPE.get(),
               check_ext=lambda e, p: check_ext_password(e, p),
               names=lambda: {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])})
TRAIN.CTX.update(dir=DATA_DIR, tz=TZ, scope=lambda: SCOPE.get(), check_ext=lambda e, p: check_ext_password(e, p), rubric=lambda: RUBRIC.load(), teams=_teams_file,
                 names=lambda: {str(r.get("id")): r.get("name", "") for r in unscoped(config).get("reps", [])},
                 analysis=lambda a, b: unscoped(analysis, a, b), calls=lambda a, b: unscoped(calls, a, b),
                 insights=lambda a, b: unscoped(insights, a, b))
