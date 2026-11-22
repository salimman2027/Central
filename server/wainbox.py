"""صندوق واتساب الشركة (WhatsApp Business Cloud API من Meta):
- كل الرسايل بتطلع وتوصل على رقم واحد للشركة، من شاشة التطبيق/اللوحة — مش من واتساب الموظف.
- كل محادثة ليها مندوب (صاحب العميل أو أول واحد كلّمه) — المندوب بيشوف عملاؤه بس، والمدير بيشوف الكل.
- كل حاجة متسجلة على السيرفر (نص، صور، ملفات، صوت، حالة الوصول/القراءة) — مفيش مسح.
- خارج نافذة الـ 24 ساعة (العميل مابعتش من يوم) واتساب بيسمح بقوالب Meta المعتمدة بس."""
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
GRAPH = "https://graph.facebook.com/v21.0"
CTX = {"dir": Path("/var/lib/nq-calls"), "check_ext": lambda e, p: False, "names": lambda: {},
       "owner_of": lambda n: None, "scope": lambda: None, "last_caller": lambda n: None, "notify": lambda ext, t, b: None}
_TPL = {"at": 0, "items": []}


def cfg() -> dict:
    return {"token": os.environ.get("NQ_WA_TOKEN", ""), "phone_id": os.environ.get("NQ_WA_PHONE_ID", ""),
            "waba": os.environ.get("NQ_WA_WABA_ID", ""), "verify": os.environ.get("NQ_WA_VERIFY", ""),
            "secret": os.environ.get("NQ_WA_APP_SECRET", "")}


def ready() -> bool:
    c = cfg()
    return bool(c["token"] and c["phone_id"])


def digits(n) -> str:
    d = re.sub(r"\D", "", str(n or ""))
    if d.startswith("00"):
        d = d[2:]
    if re.fullmatch(r"05\d{8}", d):
        d = "966" + d[1:]
    elif re.fullmatch(r"5\d{8}", d):
        d = "966" + d
    elif re.fullmatch(r"01[0125]\d{8}", d):
        d = "20" + d[1:]
    return d


def local(n: str) -> str:
    """966551234567 → 0551234567 (زي ما الموظفين متعودين)"""
    return "0" + n[3:] if n.startswith("966") and len(n) == 12 else ("+" + n if n else n)


# ------------------------------------------------------------------ التخزين (SQLite)
_DB = threading.local()


def db() -> sqlite3.Connection:
    c = getattr(_DB, "c", None)
    if c is None:
        c = sqlite3.connect(CTX["dir"] / "wa_inbox.db", timeout=15, check_same_thread=False, isolation_level=None)  # autocommit
        c.row_factory = sqlite3.Row
        c.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS convs(phone TEXT PRIMARY KEY, name TEXT DEFAULT '', ext TEXT DEFAULT '',
             last_at TEXT DEFAULT '', last_in TEXT DEFAULT '', last_text TEXT DEFAULT '', unread INTEGER DEFAULT 0,
             admin_unread INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, dir TEXT, ext TEXT DEFAULT '',
             type TEXT, body TEXT DEFAULT '', media_id TEXT DEFAULT '', file TEXT DEFAULT '', filename TEXT DEFAULT '',
             mime TEXT DEFAULT '', wamid TEXT UNIQUE, status TEXT DEFAULT '', error TEXT DEFAULT '', at TEXT);
          CREATE INDEX IF NOT EXISTS msgs_phone ON msgs(phone, id);
        """)
        _DB.c = c
    return c


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def conv(phone: str) -> dict | None:
    r = db().execute("SELECT * FROM convs WHERE phone=?", (phone,)).fetchone()
    return dict(r) if r else None


def ensure_conv(phone: str, name: str = "", ext: str = "") -> dict:
    c = conv(phone)
    if c:
        if name and not c["name"]:
            with LOCK:
                db().execute("UPDATE convs SET name=? WHERE phone=?", (name[:80], phone))
        return conv(phone)
    if not ext:   # صاحب العميل (حماية العميل) ← آخر مندوب كلّمه ← من غير مندوب (المدير يوزّعه)
        o = CTX["owner_of"](local(phone)) or {}
        ext = str(o.get("ext") or CTX["last_caller"](local(phone)) or "")
    with LOCK:
        db().execute("INSERT OR IGNORE INTO convs(phone,name,ext,last_at) VALUES(?,?,?,?)", (phone, name[:80], ext, now()))
    return conv(phone)


def window_open(c: dict | None) -> bool:
    """واتساب بيسمح برسالة عادية بس لو العميل بعت خلال آخر 24 ساعة"""
    if not c or not c.get("last_in"):
        return False
    return dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(c["last_in"]) < dt.timedelta(hours=23, minutes=50)


def add_msg(phone, direction, ext, typ, body="", media_id="", filename="", mime="", wamid=None, status="", file=""):
    with LOCK:
        cur = db().execute("INSERT OR IGNORE INTO msgs(phone,dir,ext,type,body,media_id,file,filename,mime,wamid,status,at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (phone, direction, ext, typ, body, media_id, file, filename, mime, wamid, status, now()))
        if not cur.rowcount:
            return None
        preview = body[:120] if body else {"image": "📷 صورة", "document": "📎 " + (filename or "ملف"), "audio": "🎤 رسالة صوتية",
                                           "video": "🎬 فيديو", "location": "📍 موقع", "sticker": "🙂 ستيكر"}.get(typ, typ)
        if direction == "in":
            db().execute("UPDATE convs SET last_at=?, last_in=?, last_text=?, unread=unread+1, admin_unread=admin_unread+1 WHERE phone=?",
                         (now(), now(), preview, phone))
        else:
            db().execute("UPDATE convs SET last_at=?, last_text=? WHERE phone=?", (now(), "↩ " + preview, phone))
        db().commit()
        return cur.lastrowid


# ------------------------------------------------------------------ Graph API
def graph(method: str, path: str, body: dict | None = None, raw: bytes | None = None, ctype: str = ""):
    c = cfg()
    if not ready():
        raise HTTPException(400, "واتساب الشركة مش متربط — على السيرفر: sudo nq-setkey whatsapp")
    url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + c["token"], **({"Content-Type": ctype or "application/json"} if data is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read()
            return b if r.headers.get_content_type() not in ("application/json", "text/javascript") else json.loads(b or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(e)
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise HTTPException(502, "واتساب رفض: " + msg[:300])
    except OSError as e:
        raise HTTPException(502, "مش قادر أوصل لواتساب: " + str(e)[:200])


def templates() -> list:
    """قوالب Meta المعتمدة (للرسايل بعد ما تعدّي 24 ساعة)"""
    if time.time() - _TPL["at"] < 600 and _TPL["items"]:
        return _TPL["items"]
    c = cfg()
    if not (ready() and c["waba"]):
        return []
    try:
        j = graph("GET", f"{c['waba']}/message_templates?status=APPROVED&limit=100&fields=name,language,components,category")
    except HTTPException:
        return _TPL["items"]
    out = []
    for t in j.get("data") or []:
        body = next((x.get("text", "") for x in t.get("components") or [] if x.get("type") == "BODY"), "")
        out.append({"name": t["name"], "lang": t.get("language", "ar"), "body": body, "category": t.get("category", ""),
                    "params": len(set(re.findall(r"\{\{\s*(\d+)\s*\}\}", body)))})
    _TPL.update(at=time.time(), items=out)
    return out


def _media_dir() -> Path:
    p = CTX["dir"] / "wa-media"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fetch_media(msg_id: int, media_id: str):
    """بنحفظ الصورة/الملف عندنا (روابط Meta بتخلص بعد شوية)"""
    try:
        meta = graph("GET", media_id)
        blob = graph("GET", meta["url"])
        ext = mimetypes.guess_extension((meta.get("mime_type") or "").split(";")[0]) or ".bin"
        f = _media_dir() / f"{msg_id}{ext}"
        f.write_bytes(blob if isinstance(blob, bytes) else json.dumps(blob).encode())
        with LOCK:
            db().execute("UPDATE msgs SET file=?, mime=? WHERE id=?", (f.name, meta.get("mime_type", ""), msg_id))
            db().commit()
    except Exception as e:  # noqa: BLE001
        print("wa media failed:", e, flush=True)


def send(phone: str, ext: str, payload: dict, typ: str, body: str, filename: str = "", file: str = "", mime: str = "") -> dict:
    j = graph("POST", f"{cfg()['phone_id']}/messages", {"messaging_product": "whatsapp", "recipient_type": "individual",
                                                         "to": phone, **payload})
    wamid = ((j.get("messages") or [{}])[0]).get("id") or f"local-{time.time()}"
    mid = add_msg(phone, "out", ext, typ, body, filename=filename, wamid=wamid, status="sent", file=file, mime=mime)
    return {"id": mid, "wamid": wamid}


# ------------------------------------------------------------------ Webhook (Meta بيكلّمنا)
@router.get("/app/wa-hook")
def hook_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and cfg()["verify"] and hmac.compare_digest(q.get("hub.verify_token", ""), cfg()["verify"]):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "bad verify token")


HOOK = {"hits": 0, "last": "", "lastOk": "", "lastErr": "", "msgs": 0, "statuses": 0}


def _hook_note(**k):
    HOOK.update(k)
    try:
        (CTX["dir"] / "wa_hook.json").write_text(json.dumps(HOOK, ensure_ascii=False))
    except OSError:
        pass


@router.post("/app/wa-hook")
async def hook(request: Request):
    raw = await request.body()
    HOOK["hits"] += 1
    sec = cfg()["secret"]
    if sec:
        sig = request.headers.get("x-hub-signature-256", "")
        good = "sha256=" + hmac.new(sec.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            _hook_note(last=now(), lastErr="توقيع غلط (App Secret مش مطابق) — الرسايل الجاية اترفضت")
            raise HTTPException(403, "bad signature")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {"ok": True}
    _hook_note(last=now(), lastOk=now(), lastErr="")
    for ent in data.get("entry") or []:
        for ch in ent.get("changes") or []:
            v = ch.get("value") or {}
            HOOK["msgs"] += len(v.get("messages") or [])
            HOOK["statuses"] += len(v.get("statuses") or [])
            for er in v.get("errors") or []:
                _hook_note(lastErr="واتساب: " + str(er.get("message") or er.get("title") or er)[:200])
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in v.get("contacts") or []}
            for m in v.get("messages") or []:
                try:
                    _incoming(m, names.get(m.get("from"), ""))
                except Exception as e:  # noqa: BLE001
                    print("wa incoming failed:", e, flush=True)
            for st in v.get("statuses") or []:
                err = "; ".join((x.get("error_data") or {}).get("details") or x.get("title", "") for x in st.get("errors") or [])
                with LOCK:
                    db().execute("UPDATE msgs SET status=?, error=? WHERE wamid=?", (st.get("status", ""), err[:300], st.get("id")))
                    db().commit()
    return {"ok": True}


def _incoming(m: dict, profile: str):
    phone, typ = digits(m.get("from")), m.get("type", "text")
    c = ensure_conv(phone, profile)
    body, media_id, filename, mime = "", "", "", ""
    if typ == "text":
        body = (m.get("text") or {}).get("body", "")
    elif typ in ("image", "document", "audio", "video", "sticker"):
        x = m.get(typ) or {}
        media_id, filename, mime, body = x.get("id", ""), x.get("filename", ""), x.get("mime_type", ""), x.get("caption", "")
    elif typ == "location":
        x = m.get("location") or {}
        body = f"https://maps.google.com/?q={x.get('latitude')},{x.get('longitude')} {x.get('name') or ''}".strip()
    elif typ == "button":
        body = (m.get("button") or {}).get("text", "")
    elif typ == "interactive":
        x = m.get("interactive") or {}
        body = (x.get("button_reply") or x.get("list_reply") or {}).get("title", "")
    elif typ == "reaction":
        typ, body = "text", "تفاعل: " + (m.get("reaction") or {}).get("emoji", "")
    else:
        body = f"({typ})"
    mid = add_msg(phone, "in", "", typ, body, media_id=media_id, filename=filename, mime=mime, wamid=m.get("id"))
    if mid and media_id:
        threading.Thread(target=fetch_media, args=(mid, media_id), daemon=True).start()
    if mid and c and c.get("ext"):
        CTX["notify"](c["ext"], "💬 " + (c.get("name") or local(phone)), body[:100] or "رسالة جديدة")


# ------------------------------------------------------------------ مشترك
def _conv_out(r: dict, names: dict) -> dict:
    return {"phone": r["phone"], "local": local(r["phone"]), "name": r["name"], "ext": r["ext"],
            "rep": names.get(r["ext"], r["ext"]), "lastAt": r["last_at"], "lastText": r["last_text"],
            "unread": r["unread"], "adminUnread": r["admin_unread"], "window": window_open(r)}


def list_convs(exts: set | None, q: str = "", ext: str = "", limit: int = 200) -> list:
    sql, args = "SELECT * FROM convs WHERE 1=1", []
    if exts is not None:
        sql += f" AND ext IN ({','.join('?' * len(exts))})" if exts else " AND 0"
        args += list(exts)
    if ext:
        sql += " AND ext=?" if ext != "-" else " AND ext=''"
        args += [] if ext == "-" else [ext]
    if q:
        sql += " AND (phone LIKE ? OR name LIKE ? OR last_text LIKE ?)"
        args += [f"%{digits(q) or q}%", f"%{q}%", f"%{q}%"]
    names = CTX["names"]()
    return [_conv_out(dict(r), names) for r in db().execute(sql + " ORDER BY last_at DESC LIMIT ?", (*args, limit)).fetchall()]


def thread(phone: str, before: int = 0) -> list:
    rows = db().execute("SELECT * FROM msgs WHERE phone=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT 200",
                        (phone, before, before)).fetchall()
    names = CTX["names"]()
    return [{"id": r["id"], "dir": r["dir"], "by": names.get(r["ext"], r["ext"]) if r["ext"] else "", "type": r["type"],
             "body": r["body"], "filename": r["filename"], "mime": r["mime"], "hasFile": bool(r["file"]),
             "status": r["status"], "error": r["error"], "at": r["at"]} for r in reversed(rows)]


def do_send(phone: str, ext: str, b: dict) -> dict:
    if not re.fullmatch(r"\d{8,15}", phone or ""):
        raise HTTPException(400, "رقم العميل غلط")
    c = ensure_conv(phone, str(b.get("name") or ""), ext if ext != "admin" else "")
    if c["ext"] and ext not in ("admin", c["ext"]):
        raise HTTPException(403, "العميل ده تبع مندوب تاني")
    if not c["ext"] and ext != "admin":
        with LOCK:
            db().execute("UPDATE convs SET ext=? WHERE phone=?", (ext, phone))
            db().commit()
    t = b.get("template")
    if t:
        params = [str(x) for x in t.get("params") or []]
        comp = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
        body = next((x["body"] for x in templates() if x["name"] == t.get("name")), t.get("name", ""))
        for i, p in enumerate(params, 1):
            body = body.replace("{{%d}}" % i, p)
        return send(phone, ext, {"type": "template", "template": {"name": t["name"], "language": {"code": t.get("lang") or "ar"},
                                                                  **({"components": comp} if comp else {})}}, "template", body)
    if not window_open(c):
        raise HTTPException(409, "العميل مابعتش من أكتر من 24 ساعة — واتساب بيسمح بقالب معتمد بس")
    if b.get("doc"):
        d = b["doc"]
        return send(phone, ext, {"type": "document", "document": {"link": d["url"], "filename": d.get("filename") or "file"}},
                    "document", b.get("text", ""), filename=d.get("filename", ""))
    text = str(b.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "اكتب الرسالة")
    return send(phone, ext, {"type": "text", "text": {"body": text[:4000], "preview_url": True}}, "text", text)


def do_upload(phone: str, ext: str, data: bytes, filename: str, mime: str, caption: str = "") -> dict:
    c = ensure_conv(phone, "", ext if ext != "admin" else "")
    if c["ext"] and ext not in ("admin", c["ext"]):
        raise HTTPException(403, "العميل ده تبع مندوب تاني")
    if not window_open(c):
        raise HTTPException(409, "العميل مابعتش من أكتر من 24 ساعة — ابعت قالب الأول")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "الملف أكبر من 15 ميجا")
    bnd = "----nq" + hashlib.md5(data[:64] + str(time.time()).encode()).hexdigest()
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{urllib.parse.quote(filename)}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{bnd}--\r\n".encode()
    up = graph("POST", f"{cfg()['phone_id']}/media", raw=body, ctype=f"multipart/form-data; boundary={bnd}")
    typ = "image" if mime.startswith("image/") else "audio" if mime.startswith("audio/") else "video" if mime.startswith("video/") else "document"
    obj = {"id": up["id"], **({"caption": caption} if caption and typ in ("image", "document", "video") else {}),
           **({"filename": filename} if typ == "document" else {})}
    r = send(phone, ext, {"type": typ, typ: obj}, typ, caption, filename=filename, mime=mime)
    f = _media_dir() / f"{r['id']}{mimetypes.guess_extension(mime) or '.bin'}"
    f.write_bytes(data)
    with LOCK:
        db().execute("UPDATE msgs SET file=? WHERE id=?", (f.name, r["id"]))
        db().commit()
    return r


def media_file(msg_id: int):
    r = db().execute("SELECT phone,file,filename,mime FROM msgs WHERE id=?", (msg_id,)).fetchone()
    if not r or not r["file"]:
        raise HTTPException(404, "الملف لسه بيتحمّل أو مش موجود")
    return r, _media_dir() / r["file"]


def unread_for(ext: str) -> int:
    r = db().execute("SELECT COALESCE(SUM(unread),0) n FROM convs WHERE ext=?", (ext,)).fetchone()
    return int(r["n"] or 0)


# ------------------------------------------------------------------ اللوحة (المدير والقادة)
def _scope_exts():
    sc = CTX["scope"]()
    return None if sc is None else {str(e) for e in sc["exts"]}


@router.get("/api/wai/diag")
def api_diag():
    """فحص الربط: التوكن، الرقم، اشتراك الـ webhook، آخر حاجة وصلت من Meta، وآخر غلطات الإرسال"""
    c, out = cfg(), {"ready": ready(), "phoneId": bool(c["phone_id"]), "waba": bool(c["waba"]), "verify": bool(c["verify"]),
                     "secret": bool(c["secret"]), "checks": []}
    def chk(name, fn):
        try:
            out["checks"].append({"name": name, "ok": True, **fn()})
        except HTTPException as e:
            out["checks"].append({"name": name, "ok": False, "err": str(e.detail)})
        except Exception as e:  # noqa: BLE001
            out["checks"].append({"name": name, "ok": False, "err": str(e)[:200]})
    if ready():
        def phone():
            j = graph("GET", f"{c['phone_id']}?fields=display_phone_number,verified_name,quality_rating,code_verification_status,name_status,status,platform_type,throughput")
            ok = (j.get("status") in (None, "CONNECTED")) and j.get("platform_type", "CLOUD_API") == "CLOUD_API"
            return {"ok": ok, "info": {k: j.get(k) for k in ("display_phone_number", "verified_name", "status", "quality_rating", "name_status", "code_verification_status", "platform_type")}}
        chk("الرقم", phone)
        def tok():
            j = graph("GET", f"debug_token?input_token={urllib.parse.quote(c['token'])}")
            d = j.get("data") or {}
            exp = d.get("expires_at") or 0
            return {"ok": bool(d.get("is_valid")), "info": {"valid": d.get("is_valid"), "type": d.get("type"),
                    "expires": ("مابيخلصش" if not exp else dt.datetime.fromtimestamp(exp, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")),
                    "scopes": d.get("scopes")}}
        chk("التوكن", tok)
        if c["waba"]:
            def sub():
                j = graph("GET", f"{c['waba']}/subscribed_apps")
                apps = [((a.get("whatsapp_business_api_data") or {}).get("name") or a.get("name") or "app") for a in j.get("data") or []]
                return {"ok": bool(apps), "info": {"apps": apps}, **({} if apps else {"err": "الـ WABA مش مشترك في أي App — رسايل العملاء مش هتوصلنا"})}
            chk("اشتراك الرسايل الواردة", sub)
    try:
        h = json.loads((CTX["dir"] / "wa_hook.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        h = dict(HOOK)
    out["hook"] = h
    rows = db().execute("SELECT phone, dir, type, status, error, at FROM msgs ORDER BY id DESC LIMIT 8").fetchall()
    out["recent"] = [dict(r) for r in rows]
    out["counts"] = dict(db().execute("SELECT SUM(dir='in') AS inn, SUM(dir='out') AS outt, SUM(status='failed') AS failed FROM msgs").fetchone())
    return out


@router.post("/api/wai/repair")
async def api_repair():
    """يشترك الـ App في رسايل الـ WABA (لو مش مشترك) + يمسح المحادثة الفاضية"""
    c = cfg()
    res = {}
    if c["waba"]:
        res["subscribe"] = graph("POST", f"{c['waba']}/subscribed_apps", {})
    with LOCK:
        db().execute("DELETE FROM convs WHERE phone=''")
        db().commit()
    return {"ok": True, **res}


@router.get("/api/wai/status")
def api_status():
    return {"ready": ready(), "templates": templates(), "webhook": "/calls-app/wa-hook", "verifySet": bool(cfg()["verify"])}


@router.get("/api/wai/convs")
def api_convs(q: str = "", ext: str = ""):
    return {"items": list_convs(_scope_exts(), q, ext), "ready": ready()}


@router.get("/api/wai/msgs")
def api_msgs(phone: str, before: int = 0):
    c = conv(phone)
    sx = _scope_exts()
    if not c or (sx is not None and c["ext"] not in sx):
        raise HTTPException(404, "مش موجود")
    if not before:
        with LOCK:
            db().execute("UPDATE convs SET admin_unread=0 WHERE phone=?", (phone,))
            db().commit()
    names = CTX["names"]()
    return {"conv": _conv_out(c, names), "items": thread(phone, before)}


@router.post("/api/wai/send")
async def api_send(request: Request):
    if CTX["scope"]() is not None:
        raise HTTPException(403, "الإرسال من اللوحة للمدير بس — المندوب بيبعت من التطبيق")
    b = await request.json()
    return do_send(digits(b.get("phone")), "admin", b)


@router.post("/api/wai/assign")
async def api_assign(request: Request):
    b = await request.json()
    phone, ext = digits(b.get("phone")), str(b.get("ext") or "")
    sx = _scope_exts()
    c = conv(phone)
    if not c or (sx is not None and (c["ext"] not in sx or ext not in sx)):
        raise HTTPException(403, "مش مسموح")
    with LOCK:
        db().execute("UPDATE convs SET ext=? WHERE phone=?", (ext, phone))
        db().commit()
    return {"ok": True}


@router.get("/api/wai/media/{mid}")
def api_media(mid: int):
    r, f = media_file(mid)
    c = conv(r["phone"])
    sx = _scope_exts()
    if sx is not None and (not c or c["ext"] not in sx):
        raise HTTPException(403)
    return FileResponse(f, media_type=r["mime"] or None, filename=r["filename"] or None, content_disposition_type="inline")


# ------------------------------------------------------------------ التطبيق (المندوب: عملاؤه بس)
async def _auth(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        raise HTTPException(401, "سجّل دخول تاني")
    return b, ext


def _mine(phone: str, ext: str) -> dict:
    c = conv(phone)
    if c and c["ext"] and c["ext"] != ext:
        raise HTTPException(403, "العميل ده تبع مندوب تاني")
    return c


@router.post("/app/wai/convs")
async def app_convs(request: Request):
    b, ext = await _auth(request)
    return JSONResponse({"ready": ready(), "items": list_convs({ext}, str(b.get("q") or "")),
                         "templates": templates() if b.get("tpl") else None}, headers=HDR)


@router.post("/app/wai/msgs")
async def app_msgs(request: Request):
    b, ext = await _auth(request)
    phone = digits(b.get("phone"))
    c = _mine(phone, ext)
    if c:
        with LOCK:
            db().execute("UPDATE convs SET unread=0 WHERE phone=?", (phone,))
            db().commit()
    names = CTX["names"]()
    return JSONResponse({"conv": _conv_out(c, names) if c else {"phone": phone, "local": local(phone), "window": False, "ext": ext},
                         "items": thread(phone) if c else [], "ready": ready()}, headers=HDR)


@router.post("/app/wai/send")
async def app_send(request: Request):
    b, ext = await _auth(request)
    phone = digits(b.get("phone"))
    _mine(phone, ext)
    return JSONResponse(do_send(phone, ext, b), headers=HDR)


@router.post("/app/wai/upload")
async def app_upload(request: Request):
    q = request.query_params
    ext, pw = request.headers.get("x-ext", ""), urllib.parse.unquote(request.headers.get("x-pass", ""))
    if not CTX["check_ext"](ext, pw):
        raise HTTPException(401, "سجّل دخول تاني")
    phone = digits(q.get("phone"))
    _mine(phone, ext)
    data = await request.body()
    name = urllib.parse.unquote(request.headers.get("x-name", "file"))[:80]
    mime = request.headers.get("content-type", "") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return JSONResponse(do_upload(phone, ext, data, name, mime, urllib.parse.unquote(q.get("caption", ""))), headers=HDR)


@router.post("/app/wai/media")
async def app_media(request: Request):
    b, ext = await _auth(request)
    r, f = media_file(int(b.get("id") or 0))
    c = conv(r["phone"])
    if not c or c["ext"] != ext:
        raise HTTPException(403)
    return FileResponse(f, media_type=r["mime"] or None, filename=r["filename"] or None, content_disposition_type="inline",
                        headers=HDR)
