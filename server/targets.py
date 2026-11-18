"""تارجت الاتصال: عملاء متحددين لكل موظف في يوم معيّن.
بيتضافوا من اللوحة (نموذج أو ملف Excel) أو من التطبيق (الموظف لنفسه).
العميل بيتحسب "اتعمل" لو اتكلّم (مكالمة اترد عليها) من يوم التارجت لحد النهارده."""
import datetime as dt
import json
import re
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
CTX = {"dir": Path("/var/lib/nq-calls"), "tz": dt.timezone(dt.timedelta(hours=3)), "calls": None,
       "names": lambda: {}, "scope": lambda: None, "check_ext": lambda e, p: False}


def _f() -> Path:
    return CTX["dir"] / "targets.json"


def load() -> list:
    try:
        return json.loads(_f().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save(items: list):
    tmp = _f().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_f())


def digits(n) -> str:
    return re.sub(r"\D", "", str(n or ""))[-9:]


def clean_phone(n) -> str:
    s = re.sub(r"[^\d+]", "", str(n or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    if s.endswith(".0"):
        s = s[:-2]
    if re.fullmatch(r"5\d{8}", s):
        s = "0" + s
    return s


def _date(v, default: dt.date) -> str:
    s = str(v or "").strip()
    if not s:
        return str(default)
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return str(dt.datetime.strptime(s[:10], fmt).date())
        except ValueError:
            pass
    raise HTTPException(400, f"التاريخ مش مفهوم: {s}")


def with_done(items: list) -> list:
    """نحسب مين اتكلّم: مكالمة اترد عليها لنفس الرقم من يوم التارجت"""
    if not items:
        return []
    d1 = min(dt.date.fromisoformat(x["date"]) for x in items)
    today = dt.datetime.now(CTX["tz"]).date()
    d1 = min(d1, today)
    talked: dict = {}
    for c in CTX["calls"](d1, today):
        if c["status"] == "تم الرد":
            d = digits(c.get("number"))
            t = dt.datetime.fromisoformat(c["time"].replace("Z", "+00:00")).astimezone(CTX["tz"]).date()
            if d and (d not in talked or t < talked[d][0]):
                talked[d] = (t, str(c["ext"]))
    out = []
    for x in items:
        hit = talked.get(digits(x["number"]))
        done = bool(hit and hit[0] >= dt.date.fromisoformat(x["date"])) or bool(x.get("manualDone"))
        out.append({**x, "done": done, "doneBy": hit[1] if hit and done and not x.get("manualDone") else ""})
    return out


def add_items(rows: list, by: str, allowed=None) -> list:
    names = CTX["names"]()
    today = dt.datetime.now(CTX["tz"]).date()
    new = []
    for r in rows:
        ext = str(r.get("ext") or "").strip()
        num = clean_phone(r.get("number"))
        cust = str(r.get("customer") or "").strip()[:120]
        if not ext or ext not in names:
            raise HTTPException(400, f"التحويلة مش موجودة: {ext or '(فاضية)'} — {cust or num}")
        if allowed is not None and ext not in allowed:
            raise HTTPException(403, f"التحويلة {ext} مش في فريقك")
        if len(digits(num)) < 7:
            raise HTTPException(400, f"الرقم ناقص أو غلط: {r.get('number')} — {cust}")
        new.append({"id": secrets.token_urlsafe(6), "date": _date(r.get("date"), today), "ext": ext,
                    "customer": cust or num, "number": num, "note": str(r.get("note") or "").strip()[:300],
                    "by": by, "created": dt.datetime.now(dt.timezone.utc).isoformat()})
    if not new:
        raise HTTPException(400, "مفيش عملاء")
    with LOCK:
        items = load()
        have = {(x["date"], x["ext"], digits(x["number"])) for x in items}
        added = [x for x in new if (x["date"], x["ext"], digits(x["number"])) not in have]
        cut = str(today - dt.timedelta(days=120))
        _save([x for x in items if x["date"] >= cut] + added)
    return added


# ------------------------------------------------------------------ اللوحة
@router.get("/api/targets")
def get_targets(date_from: str = "", date_to: str = "", ext: str = ""):
    today = dt.datetime.now(CTX["tz"]).date()
    f, t = date_from or str(today), date_to or date_from or str(today)
    sc = CTX["scope"]()
    items = [x for x in load() if f <= x["date"] <= t and (not ext or x["ext"] == ext)
             and (sc is None or x["ext"] in sc["exts"])]
    names = CTX["names"]()
    items = with_done(items)
    for x in items:
        x["name"] = names.get(x["ext"], x["ext"])
    items.sort(key=lambda x: (x["date"], x["name"], x["done"]))
    return {"items": items}


@router.post("/api/targets")
async def post_targets(request: Request):
    b = await request.json()
    sc = CTX["scope"]()
    added = add_items(b.get("items") or [], "admin" if sc is None else sc.get("user", "leader"),
                      None if sc is None else set(sc["exts"]))
    return {"added": len(added), "skipped": len(b.get("items") or []) - len(added)}


@router.post("/api/targets/delete")
async def del_targets(request: Request):
    ids = set((await request.json()).get("ids") or [])
    sc = CTX["scope"]()
    with LOCK:
        items = load()
        keep = [x for x in items if x["id"] not in ids or (sc is not None and x["ext"] not in sc["exts"])]
        _save(keep)
    return {"deleted": len(items) - len(keep)}


# ------------------------------------------------------------------ التطبيق
async def _auth(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        raise HTTPException(401, "سجّل دخول تاني")
    return b, ext


@router.post("/app/targets")
async def app_targets(request: Request):
    b, ext = await _auth(request)
    today = dt.datetime.now(CTX["tz"]).date()
    f, t = str(today - dt.timedelta(days=7)), str(today + dt.timedelta(days=30))
    items = with_done([x for x in load() if x["ext"] == ext and f <= x["date"] <= t])
    return JSONResponse({"items": items}, headers=HDR)


@router.post("/app/targets/add")
async def app_targets_add(request: Request):
    b, ext = await _auth(request)
    it = b.get("item") or {}
    added = add_items([{**it, "ext": ext}], "app:" + ext)
    return JSONResponse({"item": added[0] if added else None}, headers=HDR)


@router.post("/app/targets/update")
async def app_targets_update(request: Request):
    """الموظف يمسح عميل حطّه لنفسه، أو يعلّمه اتعمل/لسه بإيده"""
    b, ext = await _auth(request)
    tid, act = str(b.get("id") or ""), str(b.get("action") or "")
    with LOCK:
        items = load()
        x = next((y for y in items if y["id"] == tid and y["ext"] == ext), None)
        if not x:
            raise HTTPException(404, "مش موجود")
        if act == "delete":
            if not str(x.get("by", "")).startswith("app:"):
                raise HTTPException(403, "العميل ده المدير حطّه — مينفعش تمسحه")
            items.remove(x)
        elif act in ("done", "undone"):
            x["manualDone"] = act == "done"
        _save(items)
    return JSONResponse({"ok": True}, headers=HDR)
