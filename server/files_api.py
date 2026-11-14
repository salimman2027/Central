"""مشاركة الملفات بين الموظفين (زي LocalSend بس عن طريق السيرفر):
ملف أو صور أو نص → لموظف معيّن، أو للكل، أو لينك عام بيخلص بعد أسبوع."""
import datetime as dt
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

router = APIRouter()
LOCK = threading.Lock()
MAX_SIZE = 200 * 1024 * 1024
KEEP_DAYS = 30          # الملفات الداخلية
LINK_DAYS = 7           # اللينكات العامة

# بتتظبط من app.py
CTX = {"dir": Path("/var/lib/nq-calls/files"), "auth": None, "reps": None, "host": ""}


def _dir() -> Path:
    d = CTX["dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _dir() / "index.json"


def _load() -> list:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save(items: list):
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_index_path())


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _cleanup(items: list) -> list:
    keep, now = [], _now()
    for it in items:
        if dt.datetime.fromisoformat(it["expires"]) < now:
            shutil.rmtree(_dir() / it["id"], ignore_errors=True)
        else:
            keep.append(it)
    return keep


def _auth(request: Request) -> str:
    ext, pw = request.headers.get("x-ext", ""), request.headers.get("x-pass", "")
    if not CTX["auth"](ext, pw):
        raise HTTPException(401, "رقم التحويلة أو الباسورد غلط")
    return ext


def _names() -> dict:
    try:
        reps = CTX["reps"]() or {}
        return {str(r.get("id")): r.get("name", "") for r in reps.get("reps", [])}
    except Exception:  # noqa: BLE001
        return {}


def _public(it: dict, me: str, names: dict) -> dict:
    out = {k: it[k] for k in ("id", "name", "size", "mime", "kind", "from", "to", "created", "expires")}
    out["fromName"] = names.get(it["from"], "")
    out["toName"] = "الكل" if it["to"] == "all" else ("لينك" if it["to"] == "link" else names.get(it["to"], ""))
    out["mine"] = it["from"] == me
    out["seen"] = me in it.get("seenBy", [])
    if it["kind"] == "text":
        out["text"] = it.get("text", "")
    if it["to"] == "link":
        out["link"] = f"https://{CTX['host']}/calls-app/f/{it['id']}"
    return out


@router.get("/app/files/people")
def people(request: Request):
    me = _auth(request)
    names = _names()
    lst = [{"ext": e, "name": n} for e, n in sorted(names.items()) if e != me]
    return {"me": me, "meName": names.get(me, ""), "people": lst}


@router.post("/app/files/send")
async def send(request: Request):
    me = _auth(request)
    h = request.headers
    to = h.get("x-to", "")
    if to not in ("all", "link") and not re.fullmatch(r"\d{2,6}", to):
        raise HTTPException(400, "اختار هتبعت لمين")
    kind = "text" if h.get("x-kind") == "text" else "file"
    body = await request.body()
    if not body:
        raise HTTPException(400, "الملف فاضي")
    if len(body) > MAX_SIZE:
        raise HTTPException(413, "الملف أكبر من 200 ميجا")
    fid = secrets.token_urlsafe(12)
    now = _now()
    it = {"id": fid, "kind": kind, "from": me, "to": to, "created": now.isoformat(),
          "expires": (now + dt.timedelta(days=LINK_DAYS if to == "link" else KEEP_DAYS)).isoformat(),
          "seenBy": []}
    if kind == "text":
        txt = body.decode("utf-8", "replace")[:20000]
        it.update(name=(txt.strip().splitlines() or ["نص"])[0][:60], size=len(body), mime="text/plain", text=txt)
    else:
        name = Path(unquote(h.get("x-name", "") or "file")).name[:150] or "file"
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
        d = _dir() / fid
        d.mkdir()
        (d / name).write_bytes(body)
        it.update(name=name, size=len(body),
                  mime=h.get("content-type") if h.get("content-type") not in (None, "", "application/octet-stream")
                  else (mimetypes.guess_type(name)[0] or "application/octet-stream"))
    with LOCK:
        items = _cleanup(_load())
        items.append(it)
        _save(items)
    return _public(it, me, _names())


@router.get("/app/files/list")
def list_files(request: Request):
    me = _auth(request)
    names = _names()
    with LOCK:
        items = _cleanup(_load())
        _save(items)
    inbox = [_public(i, me, names) for i in items if i["from"] != me and i["to"] in (me, "all")]
    sent = [_public(i, me, names) for i in items if i["from"] == me]
    key = lambda x: x["created"]  # noqa: E731
    return {"inbox": sorted(inbox, key=key, reverse=True), "sent": sorted(sent, key=key, reverse=True),
            "unseen": sum(1 for i in inbox if not i["seen"])}


def _find(fid: str) -> dict:
    for it in _load():
        if it["id"] == fid:
            return it
    raise HTTPException(404, "الملف مش موجود أو خلصت مدته")


@router.get("/app/files/get/{fid}")
def get_file(fid: str, request: Request, dl: int = 0):
    me = _auth(request)
    it = _find(fid)
    if not (it["from"] == me or it["to"] in (me, "all", "link")):
        raise HTTPException(403, "الملف ده مش ليك")
    with LOCK:
        items = _load()
        for x in items:
            if x["id"] == fid and me not in x.setdefault("seenBy", []):
                x["seenBy"].append(me)
        _save(items)
    if it["kind"] == "text":
        return JSONResponse({"text": it.get("text", "")})
    return FileResponse(_dir() / fid / it["name"], media_type=it["mime"],
                        headers={"Content-Disposition": ("attachment" if dl else "inline") +
                                 f"; filename*=UTF-8''{quote(it['name'])}"})


@router.delete("/app/files/{fid}")
def delete_file(fid: str, request: Request):
    me = _auth(request)
    with LOCK:
        items = _load()
        it = next((x for x in items if x["id"] == fid), None)
        if not it:
            raise HTTPException(404, "مش موجود")
        if it["from"] != me:
            raise HTTPException(403, "تقدر تمسح الملفات اللي انت بعتها بس")
        items.remove(it)
        _save(items)
    shutil.rmtree(_dir() / fid, ignore_errors=True)
    return {"ok": True}


@router.get("/app/f/{fid}")
def public_link(fid: str, dl: int = 0):
    """اللينك العام — من غير باسورد، لحد ما المدة تخلص"""
    try:
        it = _find(fid)
    except HTTPException:
        return HTMLResponse(_page("اللينك ده خلص أو مش موجود", ""), 404)
    if it["to"] != "link" or dt.datetime.fromisoformat(it["expires"]) < _now():
        return HTMLResponse(_page("اللينك ده خلص أو مش موجود", ""), 404)
    if it["kind"] == "text":
        return HTMLResponse(_page("نص مبعوت من NQ", f'<pre>{html.escape(it.get("text", ""))}</pre>'))
    if dl:
        return FileResponse(_dir() / fid / it["name"], media_type=it["mime"],
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(it['name'])}"})
    size = f"{it['size'] / 1024 / 1024:.1f} MB" if it["size"] > 1024 * 1024 else f"{max(1, it['size'] // 1024)} KB"
    exp = dt.datetime.fromisoformat(it["expires"]).astimezone(dt.timezone(dt.timedelta(hours=3)))
    return HTMLResponse(_page(it["name"], f'<p class="m">{size} · متاح لحد {exp:%d/%m %H:%M}</p>'
                                          f'<a class="b" href="?dl=1">⬇ تحميل</a>'))


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,Tahoma,sans-serif;
background:radial-gradient(600px 400px at 80% 0,#19e6c133,transparent),#060a14;color:#e8f1ff}}
.c{{max-width:520px;margin:20px;padding:28px;border:1px solid #94beff26;border-radius:20px;background:#121c32aa;text-align:center}}
h1{{font-size:20px;word-break:break-all}}.m{{color:#8ea3bd}}pre{{white-space:pre-wrap;text-align:right;background:#0006;padding:14px;border-radius:12px}}
.b{{display:inline-block;margin-top:10px;padding:12px 26px;border-radius:14px;background:linear-gradient(135deg,#19e6c1,#5aa8ff);color:#04101a;font-weight:800;text-decoration:none}}
.l{{display:inline-block;background:linear-gradient(135deg,#19e6c1,#5aa8ff);color:#04101a;border-radius:10px;padding:4px 10px;font-weight:800;margin-bottom:10px}}</style></head>
<body><div class="c"><div class="l">NQ</div><h1>{html.escape(title)}</h1>{body}</div></body></html>"""
