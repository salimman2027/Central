"""ملكية العملاء والتعارضات:
أول مندوب كلّم العميل هو صاحبه. لو مندوب تاني حاول يتصل بيه → إنذار في التطبيق،
ولو أصرّ يتبعت طلب للمشرف في "التعارضات". المشرف يوافق (العميل يتنقل للمندوب الجديد)
أو يرفض (يفضل مع الأول) — واللي مش صاحب العميل ميقدرش يتصل من غير طلب جديد."""
import datetime as dt
import json
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
LOCK = threading.Lock()
LOOKBACK_DAYS = 180
# بتتظبط من app.py
CTX = {"dir": Path("/var/lib/nq-calls"), "calls": None, "names": lambda: {}, "auth": None,
       "notes": lambda a, b: [], "analysis": lambda rec: None}
HDR = {"Access-Control-Allow-Origin": "*"}


def digits(n) -> str:
    return re.sub(r"\D", "", str(n or ""))[-9:]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _jload(name: str, default):
    try:
        return json.loads((CTX["dir"] / name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _jsave(name: str, data):
    f = CTX["dir"] / name
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


# ------------------------------------------------------------------ تاريخ المكالمات (متخزّن دقيقة)
_CACHE = {"at": 0.0, "by": {}}


def _history() -> dict:
    """كل المكالمات في آخر 6 شهور، متجمّعة برقم العميل (آخر 9 أرقام)"""
    if time.time() - _CACHE["at"] < 60:
        return _CACHE["by"]
    today = dt.date.today()
    by: dict = {}
    try:
        for c in CTX["calls"](today - dt.timedelta(days=LOOKBACK_DAYS), today):
            d = digits(c.get("number"))
            if len(d) >= 7 and c.get("ext"):
                by.setdefault(d, []).append(c)
    except Exception as e:  # noqa: BLE001
        print("ownership history failed:", e, flush=True)
        return _CACHE["by"]
    for lst in by.values():
        lst.sort(key=lambda c: c["time"])
    _CACHE.update(at=time.time(), by=by)
    return by


def _contacted(c: dict) -> bool:
    """محاولة اتصال صادرة، أو مكالمة واردة اترد عليها"""
    return c.get("dir") == "out" or c.get("status") == "تم الرد"


def owner_of(number: str) -> dict | None:
    d = digits(number)
    own = _jload("owners.json", {}).get(d)
    if own:
        return {"ext": str(own["ext"]), "since": own.get("since"), "by": own.get("by", "assigned")}
    first = next((c for c in _history().get(d, []) if _contacted(c)), None)
    if first:
        return {"ext": str(first["ext"]), "since": first["time"], "by": "first"}
    return None


def _first_call(number: str, ext: str) -> dict | None:
    return next((c for c in _history().get(digits(number), []) if str(c["ext"]) == str(ext) and _contacted(c)), None)


def _last_call(number: str, ext: str) -> dict | None:
    """آخر مكالمة متحلّلة للمندوب مع العميل، ولو مفيش: آخر مكالمة اترد عليها، ولو مفيش: آخر محاولة"""
    lst = [c for c in _history().get(digits(number), []) if str(c["ext"]) == str(ext)]
    for ok in (lambda c: c.get("analyzed"), lambda c: c.get("status") == "تم الرد", lambda c: True):
        hit = [c for c in lst if ok(c)]
        if hit:
            return hit[-1]
    return None


def _customer_name(number: str) -> str:
    d = digits(number)
    for c in reversed(_history().get(d, [])):
        if (c.get("note") or {}).get("customer"):
            return c["note"]["customer"]
    return ""


def _pending(number: str, ext: str) -> dict | None:
    d = digits(number)
    return next((x for x in _jload("conflicts.json", []) if x["status"] == "pending"
                 and x["numberKey"] == d and str(x["newExt"]) == str(ext)), None)


def _auth(body: dict) -> str:
    ext, pw = str(body.get("ext") or ""), str(body.get("password") or "")
    if not CTX["auth"](ext, pw):
        raise HTTPException(401, "رقم التحويلة أو الباسورد غلط")
    return ext


# ------------------------------------------------------------------ التطبيق
@router.post("/app/owner/check")
async def check(request: Request):
    """قبل أي اتصال: هل العميل ده تبعي؟"""
    body = await request.json()
    ext = _auth(body)
    number = str(body.get("number") or "")
    if len(digits(number)) < 7:
        return JSONResponse({"allowed": True}, headers=HDR)
    own = owner_of(number)
    if not own or own["ext"] == ext:
        return JSONResponse({"allowed": True}, headers=HDR)
    names = CTX["names"]()
    first = _first_call(number, own["ext"])
    pend = _pending(number, ext)
    return JSONResponse({
        "allowed": False,
        "owner": {"ext": own["ext"], "name": names.get(own["ext"], ""), "since": own["since"], "by": own["by"],
                  "firstCall": first["time"] if first else own["since"]},
        "customer": _customer_name(number),
        "pending": bool(pend), "requestedAt": pend["requestedAt"] if pend else None,
    }, headers=HDR)


@router.post("/app/owner/request")
async def request_transfer(request: Request):
    """المندوب أصرّ يكلّم العميل → طلب للمشرف"""
    body = await request.json()
    ext = _auth(body)
    number = str(body.get("number") or "").strip()
    reason = str(body.get("reason") or "").strip()[:1000]
    if len(digits(number)) < 7:
        raise HTTPException(400, "رقم العميل مش واضح")
    if len(reason) < 3:
        raise HTTPException(400, "اكتب سبب إنك عايز تكلّم العميل ده")
    own = owner_of(number)
    if not own or own["ext"] == ext:
        return JSONResponse({"ok": True, "allowed": True}, headers=HDR)
    names = CTX["names"]()
    with LOCK:
        items = _jload("conflicts.json", [])
        old = next((x for x in items if x["status"] == "pending" and x["numberKey"] == digits(number)
                    and str(x["newExt"]) == ext), None)
        if old:
            old["reason"] = reason
            old["attempts"] = int(old.get("attempts") or 1) + 1
            old["requestedAt"] = _now()
            item = old
        else:
            first = _first_call(number, own["ext"])
            item = {
                "id": secrets.token_urlsafe(8), "status": "pending",
                "number": number, "numberKey": digits(number),
                "customer": str(body.get("customer") or "").strip()[:200] or _customer_name(number),
                "firstExt": own["ext"], "firstName": names.get(own["ext"], ""),
                "firstCall": first["time"] if first else own["since"], "ownerBy": own["by"],
                "newExt": ext, "newName": names.get(ext, ""),
                "requestedAt": _now(), "reason": reason, "attempts": 1,
            }
            items.append(item)
        _jsave("conflicts.json", items)
    return JSONResponse({"ok": True, "id": item["id"]}, headers=HDR)


# ------------------------------------------------------------------ اللوحة (المشرف)
def _call_brief(c: dict | None) -> dict | None:
    if not c:
        return None
    out = {k: c.get(k) for k in ("id", "time", "dir", "status", "talkSec", "hasRecording", "analyzed")}
    out["note"] = c.get("note")
    a = CTX["analysis"](c.get("rec") or "") if c.get("analyzed") else None
    if a:
        out["analysis"] = {k: a.get(k) for k in ("agent_score", "outcome", "close_probability", "summary",
                                                  "sentiment_end", "coaching_tip", "no_buy_reason")}
    return out


@router.get("/api/conflicts")
def list_conflicts(status: str = ""):
    items = _jload("conflicts.json", [])
    names = CTX["names"]()
    out = []
    for x in items:
        if status and x["status"] != status:
            continue
        y = dict(x)
        y["firstName"] = names.get(str(x["firstExt"]), x.get("firstName", ""))
        y["newName"] = names.get(str(x["newExt"]), x.get("newName", ""))
        hist = _history().get(x["numberKey"], [])
        y["firstLast"] = _call_brief(_last_call(x["number"], x["firstExt"]))
        y["newLast"] = _call_brief(_last_call(x["number"], x["newExt"]))
        y["firstCalls"] = sum(1 for c in hist if str(c["ext"]) == str(x["firstExt"]))
        y["newCalls"] = sum(1 for c in hist if str(c["ext"]) == str(x["newExt"]))
        own = owner_of(x["number"])
        y["ownerNow"] = own["ext"] if own else None
        out.append(y)
    out.sort(key=lambda v: (v["status"] != "pending", v.get("decidedAt") or v["requestedAt"]), reverse=False)
    pend = [v for v in out if v["status"] == "pending"]
    rest = sorted([v for v in out if v["status"] != "pending"], key=lambda v: v.get("decidedAt") or "", reverse=True)
    return {"items": pend + rest, "counts": {s: sum(1 for v in items if v["status"] == s)
                                             for s in ("pending", "approved", "rejected")}}


def _decide(cid: str, approve: bool, note: str):
    with LOCK:
        items = _jload("conflicts.json", [])
        it = next((x for x in items if x["id"] == cid), None)
        if not it:
            raise HTTPException(404, "الطلب مش موجود")
        if it["status"] != "pending":
            raise HTTPException(400, "الطلب ده اتقرر قبل كده")
        winner = str(it["newExt"]) if approve else str(it["firstExt"])
        owners = _jload("owners.json", {})
        owners[it["numberKey"]] = {"ext": winner, "since": _now(), "by": "approved" if approve else "kept",
                                   "conflict": it["id"]}
        _jsave("owners.json", owners)
        it.update(status="approved" if approve else "rejected", decidedAt=_now(), decisionNote=note[:500])
        # أي طلبات تانية مستنية على نفس العميل بتتقفل بنفس القرار
        for x in items:
            if x is not it and x["status"] == "pending" and x["numberKey"] == it["numberKey"] and str(x["newExt"]) == winner:
                x.update(status="approved", decidedAt=_now(), decisionNote="اتقفل مع طلب تاني على نفس العميل")
        _jsave("conflicts.json", items)
        return it


@router.post("/api/conflicts/{cid}/approve")
async def approve(cid: str, request: Request):
    body = await _body(request)
    return _decide(cid, True, str(body.get("note") or ""))


@router.post("/api/conflicts/{cid}/reject")
async def reject(cid: str, request: Request):
    body = await _body(request)
    return _decide(cid, False, str(body.get("note") or ""))


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}
