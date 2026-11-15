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
        for k in ("date", "time", "customer", "number", "details", "kind"):
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
def crm_configured() -> bool:
    return bool(os.environ.get("NQ_CRM_URL"))


def push_crm(it: dict) -> dict:
    """بيبعت الموعد المتأكد للـ CRM (بيتظبط لما نعرف الـ endpoint من الـ Swagger)"""
    url = os.environ.get("NQ_CRM_URL", "")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if not url:
        return {"ok": False, "pending": True, "at": now, "msg": "الربط مع الـ CRM لسه متظبطش"}
    import requests
    when = f"{it['date']}T{(it.get('time') or '09:00')}:00"
    body = {
        "customerName": it.get("customer"), "customerPhone": it.get("number"),
        "appointmentDate": when, "type": it.get("kind"), "notes": it.get("details") or it.get("quote"),
        "salesRepName": it.get("repName"), "salesRepExtension": it.get("ext"), "source": "NQ Calls",
    }
    headers = {"Content-Type": "application/json"}
    tok = os.environ.get("NQ_CRM_TOKEN", "")
    if tok:
        headers[os.environ.get("NQ_CRM_AUTH_HEADER", "Authorization")] = (os.environ.get("NQ_CRM_AUTH_PREFIX", "Bearer ") + tok)
    try:
        r = requests.post(url, json=body, headers=headers, timeout=30)
        return {"ok": r.status_code < 300, "code": r.status_code, "at": now, "msg": r.text[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "at": now, "msg": str(e)[:300]}
