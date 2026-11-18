"""واتساب بعد المكالمة: قوالب رسايل (وممكن ملف زي كتالوج أو عرض سعر) بيحطها المدير من اللوحة،
والموظف بيبعتها للعميل بزرار واحد (بيفتح واتساب بالرسالة جاهزة ومعاها رابط الملف)."""
import datetime as dt
import json
import re
import secrets
import threading
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
CTX = {"dir": Path("/var/lib/nq-calls"), "host": "3.89.245.93.sslip.io", "check_ext": lambda e, p: False,
       "names": lambda: {}}
MAX_FILE = 15 * 1024 * 1024
DEFAULT = [{"id": "intro", "title": "👋 تعريف بعد المكالمة",
            "text": "السلام عليكم {العميل}،\nمعك {الموظف} من الجودة الوطنية لتقنية المعلومات.\nسعدت بالحديث معك، وهذي تفاصيل البرنامج:\n{الرابط}\nأي استفسار أنا في الخدمة 🌹",
            "file": None}]


def _f() -> Path:
    return CTX["dir"] / "wa.json"


def _files() -> Path:
    p = CTX["dir"] / "wa-files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load() -> list:
    try:
        return json.loads(_f().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return [dict(x) for x in DEFAULT]


def _save(items):
    _f().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def public(items: list) -> list:
    out = []
    for t in items:
        f = t.get("file")
        safe = ""
        if f:   # الرابط بحروف إنجليزي بس عشان واتساب يفهمه لينك كامل (اسم الملف الحقيقي بيظهر للعميل لما يفتحه)
            ext = Path(f["name"]).suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,5}", Path(f["name"]).suffix.lower()) else ""
            base = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(f["name"]).stem).strip("-")[:40] or "NQ"
            safe = base + ext
        out.append({**t, "url": f"https://{CTX['host']}/calls-app/wa/f/{f['token']}/{safe}" if f else ""})
    return out


def digits(n) -> str:
    return re.sub(r"\D", "", str(n or ""))[-9:]


def log_for(number: str) -> dict | None:
    """آخر رسالة واتساب اتبعتت للرقم ده (لبطاقة العميل)"""
    p = CTX["dir"] / "wa-log.jsonl"
    if not p.exists():
        return None
    d, last = digits(number), None
    for ln in p.read_text(encoding="utf-8").splitlines()[-5000:]:
        try:
            x = json.loads(ln)
        except ValueError:
            continue
        if x.get("d") == d:
            last = x
    return last


# ------------------------------------------------------------------ اللوحة
@router.get("/api/wa")
def get_wa():
    return {"items": public(load())}


@router.put("/api/wa")
async def put_wa(request: Request):
    b = await request.json()
    old = {t["id"]: t for t in load()}
    items = []
    for t in b.get("items") or []:
        title, text = str(t.get("title") or "").strip()[:80], str(t.get("text") or "").strip()[:3000]
        if not title or not text:
            raise HTTPException(400, "كل قالب لازم يبقى ليه اسم ونص")
        tid = str(t.get("id") or "") or secrets.token_urlsafe(5)
        f = t.get("file")
        if f and not (_files() / f"{f.get('token')}").exists():
            f = None
        items.append({"id": tid, "title": title, "text": text, "file": f and {"token": f["token"], "name": f["name"], "size": f.get("size")}})
    keep = {(t.get("file") or {}).get("token") for t in items}
    for t in old.values():   # الملفات اللي اتشالت
        tok = (t.get("file") or {}).get("token")
        if tok and tok not in keep:
            (_files() / tok).unlink(missing_ok=True)
    _save(items)
    return {"items": public(items)}


@router.post("/api/wa/file")
async def wa_file(request: Request):
    name = re.sub(r"[^\w.\- ()؀-ۿ]", "_", urllib.parse.unquote(request.headers.get("x-name", "file")))[:80] or "file"
    data = await request.body()
    if not data:
        raise HTTPException(400, "الملف فاضي")
    if len(data) > MAX_FILE:
        raise HTTPException(400, "الملف أكبر من 15 ميجا")
    tok = secrets.token_urlsafe(12)
    (_files() / tok).write_bytes(data)
    return {"token": tok, "name": name.replace(" ", "-"), "size": len(data)}


@router.get("/api/wa/stats")
def wa_stats(days: int = 7):
    p = CTX["dir"] / "wa-log.jsonl"
    cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    by: dict = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines()[-20000:]:
            try:
                x = json.loads(ln)
            except ValueError:
                continue
            if x.get("at", "") >= cut:
                k = (x.get("ext"), x.get("title"))
                by[k] = by.get(k, 0) + 1
    names = CTX["names"]()
    return {"items": [{"ext": e, "name": names.get(e, e), "title": t, "n": n} for (e, t), n in sorted(by.items(), key=lambda kv: -kv[1])]}


# ------------------------------------------------------------------ عام: الملف نفسه (العميل بيفتحه من الرابط)
@router.get("/app/wa/f/{token}/{name}")
def wa_public(token: str, name: str):
    if not re.fullmatch(r"[\w-]{10,40}", token):
        raise HTTPException(404)
    t = next((t for t in load() if (t.get("file") or {}).get("token") == token), None)
    p = _files() / token
    if not t or not p.exists():
        raise HTTPException(404, "الملف ده اتشال")
    return FileResponse(p, filename=t["file"]["name"], content_disposition_type="inline")


# ------------------------------------------------------------------ التطبيق
@router.post("/app/wa")
async def app_wa(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    if b.get("log"):
        lg = b["log"]
        rec = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "ext": ext, "d": digits(lg.get("number")),
               "number": str(lg.get("number") or "")[:20], "title": str(lg.get("title") or "")[:80]}
        with LOCK, open(CTX["dir"] / "wa-log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return JSONResponse({"ok": True}, headers=HDR)
    return JSONResponse({"items": public(load()), "me": CTX["names"]().get(ext, "")}, headers=HDR)
