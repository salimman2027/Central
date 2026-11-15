"""طلبات تحديد موعد: لو المندوب كتب أو سجّل بصوته ملاحظة فيها ميعاد مع العميل،
السيستم بيطلّعه لوحده ويحطه في "طلبات تحديد موعد" — والمدير يأكده، ولما يتأكد يتبعت للـ CRM."""
import datetime as dt
import json
import os
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
LOCK = threading.Lock()
RIYADH = dt.timezone(dt.timedelta(hours=3))
CTX = {"dir": Path("/var/lib/nq-calls"), "names": lambda: {}, "notes": lambda a, b: []}

PROMPT = """إنت بتقرا ملاحظة كتبها (أو سجّلها بصوته) مندوب مبيعات بعد مكالمة مع عميل.
مهمتك: تشوف هل فيها **ميعاد متفق عليه مع العميل** (زيارة، اجتماع، ديمو، مكالمة تانية في وقت محدد، تسليم…).
- "هفكر" أو "كلمني بعدين" من غير وقت = مش ميعاد.
- "بكرة الساعة 10" أو "يوم الأحد العصر" أو "الأسبوع الجاي" = ميعاد — حوّله لتاريخ حقيقي.
- النهارده: {today} ({weekday})، ووقت الملاحظة: {now} بتوقيت السعودية.
- لو اليوم معروف والساعة لأ، حط الساعة null.

بيانات المكالمة: {meta}
الملاحظة:
<note>
{text}
</note>

رجّع JSON بس:
{{"has_appointment": true, "date": "YYYY-MM-DD أو null", "time": "HH:MM أو null",
  "kind": "زيارة | اجتماع | ديمو | مكالمة | تسليم | أخرى", "customer_name": "لو اتذكر أو null",
  "quote": "الجملة اللي فيها الميعاد من الملاحظة", "details": "تفاصيل مهمة قصيرة (المكان، الموضوع) أو null"}}"""

WEEKDAYS = ["الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت", "الحد"]


def _file() -> Path:
    return CTX["dir"] / "appointments.json"


def _load() -> list:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save(items: list):
    _file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_file())


def note_key(note: dict) -> str:
    return f"{note.get('ext')}|{note.get('started') or note.get('savedAt') or note.get('received')}"


def extract(note: dict) -> dict | None:
    """بيسأل Claude لو الملاحظة فيها ميعاد"""
    text = "\n".join(x for x in (note.get("summary"), note.get("followUp") and f"ميعاد المتابعة: {note['followUp']}") if x)
    if not text.strip() or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    now = dt.datetime.now(RIYADH)
    try:
        when = dt.datetime.fromisoformat(str(note.get("started") or note.get("received")).replace("Z", "+00:00")).astimezone(RIYADH)
    except Exception:  # noqa: BLE001
        when = now
    meta = {k: note.get(k) for k in ("customer", "number", "outcome", "direction")}
    msg = anthropic.Anthropic().messages.create(
        model=os.environ.get("NQ_CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=3000,
        messages=[{"role": "user", "content": PROMPT.format(
            today=when.date().isoformat(), weekday=WEEKDAYS[when.weekday()], now=when.strftime("%H:%M"),
            meta=json.dumps(meta, ensure_ascii=False), text=text[:4000])}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    a = json.loads(raw)
    return a if a.get("has_appointment") else None


def process_note(note: dict) -> dict | None:
    """بيتنادى بعد ما الملاحظة تتحفظ — لو فيها ميعاد يتعمل طلب"""
    key = note_key(note)
    with LOCK:
        items = _load()
        if any(x.get("noteKey") == key for x in items):
            return None
    try:
        a = extract(note)
    except Exception as e:  # noqa: BLE001
        print("appointment extract failed:", e, flush=True)
        return None
    if not a:
        return None
    ext = str(note.get("ext") or "")
    item = {
        "id": secrets.token_urlsafe(8), "noteKey": key, "status": "pending",
        "created": dt.datetime.now(dt.timezone.utc).isoformat(),
        "ext": ext, "repName": CTX["names"]().get(ext, ""),
        "customer": note.get("customer") or a.get("customer_name") or "",
        "number": note.get("number") or "",
        "date": a.get("date"), "time": a.get("time"), "kind": a.get("kind") or "",
        "quote": a.get("quote") or "", "details": a.get("details") or "",
        "summary": (note.get("summary") or "")[:1500], "callStarted": note.get("started"),
        "crm": None,
    }
    with LOCK:
        items = _load()
        if any(x.get("noteKey") == key for x in items):
            return None
        items.append(item)
        _save(items)
    return item


def process_async(note: dict):
    threading.Thread(target=process_note, args=(dict(note),), daemon=True).start()


# ------------------------------------------------------------------ API (اللوحة)
@router.get("/api/appointments")
def list_appointments(status: str = ""):
    items = _load()
    if status:
        items = [x for x in items if x["status"] == status]
    items.sort(key=lambda x: (x["status"] != "pending", x.get("date") or "9999", x.get("time") or ""))
    counts = {s: sum(1 for x in _load() if x["status"] == s) for s in ("pending", "confirmed", "rejected")}
    return {"items": items, "counts": counts, "crm": crm_configured()}


def _update(aid: str, fn):
    with LOCK:
        items = _load()
        it = next((x for x in items if x["id"] == aid), None)
        if not it:
            raise HTTPException(404, "الطلب مش موجود")
        fn(it)
        _save(items)
        return it


@router.post("/api/appointments/{aid}/confirm")
async def confirm(aid: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass

    def f(it):
        for k in ("date", "time", "customer", "number", "details", "kind", "location", "duration", "company", "email"):
            if body.get(k) is not None:
                it[k] = str(body[k])[:300]
        if not it.get("date"):
            raise HTTPException(400, "حدد تاريخ الموعد الأول")
        it["status"] = "confirmed"
        it["confirmedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    it = _update(aid, f)
    res = push_crm(it)
    return _update(aid, lambda x: x.__setitem__("crm", res))


@router.post("/api/appointments/{aid}/reject")
def reject(aid: str):
    return _update(aid, lambda it: it.update(status="rejected", rejectedAt=dt.datetime.now(dt.timezone.utc).isoformat()))


@router.post("/api/appointments/{aid}/reopen")
def reopen(aid: str):
    return _update(aid, lambda it: it.update(status="pending"))


@router.post("/api/appointments/{aid}/crm")
def retry_crm(aid: str):
    it = next((x for x in _load() if x["id"] == aid), None)
    if not it or it["status"] != "confirmed":
        raise HTTPException(400, "الطلب لازم يبقى متأكد")
    res = push_crm(it)
    return _update(aid, lambda x: x.__setitem__("crm", res))


SCAN = {"running": False, "done": 0, "found": 0, "total": 0}


@router.post("/api/appointments/scan")
def scan(date_from: dt.date, date_to: dt.date):
    """يدوّر في الملاحظات القديمة على مواعيد (مرة واحدة لكل ملاحظة)"""
    if SCAN["running"]:
        return SCAN
    notes = CTX["notes"](date_from, date_to)
    done_keys = {x.get("noteKey") for x in _load()}
    todo = [n for n in notes if note_key(n) not in done_keys and (n.get("summary") or n.get("followUp"))]
    SCAN.update(running=True, done=0, found=0, total=len(todo))

    def run():
        try:
            for n in todo:
                if process_note(n):
                    SCAN["found"] += 1
                SCAN["done"] += 1
        finally:
            SCAN["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return SCAN


@router.get("/api/appointments/scan")
def scan_status():
    return SCAN


# ------------------------------------------------------------------ CRM
def crm_settings() -> dict:
    """إعدادات الربط (من غير التوكن — ده في env بـ sudo nq-setkey crm)"""
    try:
        c = json.loads((CTX["dir"] / "crm.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        c = {}
    return {"url": c.get("url") or "https://national-quality-api-site-nq.premiumasp.net/api/v1/crm-appointments", "authHeader": c.get("authHeader", "Authorization"),
            "authPrefix": c.get("authPrefix", "Bearer "), "duration": int(c.get("duration") or 60),
            "termsManagementId": int(c.get("termsManagementId") or 0), "sendEmail": bool(c.get("sendEmail", False)),
            "salesIds": {str(k): int(v) for k, v in (c.get("salesIds") or {}).items() if str(v).strip().isdigit()}}


def save_crm_settings(c: dict) -> dict:
    cur = crm_settings()
    for k in ("url", "authHeader", "authPrefix"):
        if k in c:
            cur[k] = str(c[k] or "").strip()[:500]
    for k in ("duration", "termsManagementId"):
        if k in c:
            cur[k] = int(c[k] or 0)
    if "sendEmail" in c:
        cur["sendEmail"] = bool(c["sendEmail"])
    if "salesIds" in c:
        cur["salesIds"] = {str(k): int(v) for k, v in (c["salesIds"] or {}).items() if str(v).strip().isdigit()}
    if cur["url"] and not cur["url"].startswith("https://"):
        raise ValueError("رابط الـ CRM لازم يبدأ بـ https://")
    (CTX["dir"] / "crm.json").write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def crm_configured() -> bool:
    return bool(crm_settings()["url"])


def _add_minutes(hhmm: str, mins: int) -> str:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    t = h * 60 + m + mins
    return f"{(t // 60) % 24:02d}:{t % 60:02d}"


def crm_body(it: dict, cfg: dict) -> dict:
    """شكل الطلب زي الـ Swagger بتاع الـ CRM (Meeting)"""
    dur = int(it.get("duration") or cfg["duration"] or 60)
    start = (it.get("time") or "10:00")[:5]
    place = (it.get("location") or "").strip()
    online = place.lower().startswith("http") or it.get("kind") in ("ديمو", "مكالمة") and not place
    sales_id = cfg["salesIds"].get(str(it.get("ext")), 0)
    return {
        "id": 0, "code": "", "clientId": int(it.get("clientId") or 0),
        "date": f"{it['date']}T00:00:00", "timeFrom": start, "timeTo": _add_minutes(start, dur), "duration": dur,
        "objectives": " — ".join(x for x in (it.get("kind"), it.get("details")) if x) or "متابعة مع العميل",
        "requirement": it.get("quote") or "", "results": "",
        "employeeIds": [sales_id] if sales_id else [],
        "isPotential": not it.get("clientId"), "termsManagementId": cfg["termsManagementId"],
        "email": it.get("email") or "", "isOnlineMeet": bool(online),
        "meetingLink": place if place.lower().startswith("http") else "",
        "meetingLocation": "" if place.lower().startswith("http") else place,
        "customerNameForMeet": it.get("customer") or "", "customerPhoneForMeet": it.get("number") or "",
        "companyName": it.get("company") or it.get("customer") or "",
        "salesId": sales_id, "salesName": it.get("repName") or "",
        "details": [], "files": [], "sendEmail": cfg["sendEmail"],
    }


def push_crm(it: dict) -> dict:
    cfg = crm_settings()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if not cfg["url"]:
        return {"ok": False, "pending": True, "at": now, "msg": "الربط مع الـ CRM لسه متظبطش"}
    import requests
    # الـ API بيستخدم إصدار في نوع المحتوى (زي ما في الـ Swagger)
    headers = {"Content-Type": "application/json; ver=1.0", "Accept": "application/json; ver=1.0, application/json"}
    tok = os.environ.get("NQ_CRM_TOKEN", "").strip()
    if tok:
        pfx = (cfg["authPrefix"] or "").strip()
        tok = tok.removeprefix("Bearer ").removeprefix("bearer ").strip()
        headers[(cfg["authHeader"] or "Authorization").strip()] = f"{pfx} {tok}" if pfx else tok
    body = crm_body(it, cfg)
    try:
        r = requests.post(cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, timeout=30)
        res = {"ok": r.status_code < 300, "code": r.status_code, "at": now, "msg": r.text[:400]}
        if r.status_code == 401:
            res["msg"] = "الـ CRM طالب تسجيل دخول — حط التوكن بـ sudo nq-setkey crm"
        try:
            j = r.json()
            if isinstance(j, dict):
                data = j.get("data") if isinstance(j.get("data"), dict) else {}
                res["crmId"] = j.get("id") or data.get("id")
        except Exception:  # noqa: BLE001
            pass
        return res
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "at": now, "msg": str(e)[:300]}


@router.get("/api/crm/settings")
def get_crm():
    c = crm_settings()
    return {**c, "hasToken": bool(os.environ.get("NQ_CRM_TOKEN", "").strip())}


@router.put("/api/crm/settings")
async def put_crm(request: Request):
    try:
        return save_crm_settings(await request.json())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/crm/preview/{aid}")
def preview(aid: str):
    """يوريك الطلب اللي هيتبعت للـ CRM من غير ما يبعته"""
    it = next((x for x in _load() if x["id"] == aid), None)
    if not it:
        raise HTTPException(404, "مش موجود")
    if not it.get("date"):
        raise HTTPException(400, "مفيش تاريخ")
    return crm_body(it, crm_settings())
