"""طلبات تحديد موعد: لو المندوب كتب أو سجّل بصوته ملاحظة فيها ميعاد مع العميل،
السيستم بيطلّعه لوحده ويحطه في "طلبات تحديد موعد" — والمدير يأكده، ولما يتأكد يتبعت للـ CRM."""
import asyncio
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
    if crm_configured() and item["number"]:
        _apply_match(item, match_client(item["number"]))
    with LOCK:
        items = _load()
        if any(x.get("noteKey") == key for x in items):
            return None
        items.append(item)
        _save(items)
    return item


def _apply_match(it: dict, c: dict | None):
    if c:
        it.update(clientId=str(c["id"]), clientName=c["name"], isPotential=bool(c["potential"]), clientAuto=True)


def _ensure_client(aid: str) -> dict:
    it = next((x for x in _load() if x["id"] == aid), None)
    if it and not str(it.get("clientId") or "").strip() and it.get("number"):
        c = match_client(it["number"])
        if c:
            return _update(aid, lambda x: _apply_match(x, c))
    return it


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
        for k in ("date", "time", "customer", "number", "details", "kind", "location", "duration", "company", "email", "clientId", "clientName"):
            if body.get(k) is not None:
                it[k] = str(body[k])[:300]
        if body.get("isPotential") is not None:
            it["isPotential"] = bool(body["isPotential"])
        if not it.get("date"):
            raise HTTPException(400, "حدد تاريخ الموعد الأول")
        it["status"] = "confirmed"
        it["confirmedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _update(aid, f)
    it = await asyncio.to_thread(_ensure_client, aid)
    res = await asyncio.to_thread(push_crm, it)
    return _update(aid, lambda x: x.__setitem__("crm", res))


@router.post("/api/appointments/{aid}/client")
async def set_client(aid: str, request: Request):
    b = await request.json()
    cid = str(b.get("clientId") or "").strip()
    if not cid.isdigit():
        raise HTTPException(400, "اختار العميل")
    return _update(aid, lambda x: x.update(clientId=cid, clientName=str(b.get("clientName") or "")[:200],
                                           isPotential=bool(b.get("isPotential")), clientAuto=False))


@router.post("/api/appointments/{aid}/reject")
def reject(aid: str):
    return _update(aid, lambda it: it.update(status="rejected", rejectedAt=dt.datetime.now(dt.timezone.utc).isoformat()))


@router.post("/api/appointments/{aid}/reopen")
def reopen(aid: str):
    return _update(aid, lambda it: it.update(status="pending"))


@router.post("/api/appointments/{aid}/crm")
async def retry_crm(aid: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass
    it = next((x for x in _load() if x["id"] == aid), None)
    if not it or it["status"] != "confirmed":
        raise HTTPException(400, "الطلب لازم يبقى متأكد")
    if body.get("time"):
        def upd(x):
            x.update(time=str(body["time"])[:5], date=str(body.get("date") or x["date"])[:10])
            to = str(body.get("timeTo") or "")[:5]
            if to:   # الفترة البديلة ممكن تكون أقصر من مدة الاجتماع — ناخد الأقل
                fh, fm = (int(v) for v in x["time"].split(":")); th, tm = (int(v) for v in to.split(":"))
                slot = (th * 60 + tm) - (fh * 60 + fm)
                cur = int(x.get("duration") or crm_settings()["duration"] or 60)
                if slot > 0:
                    x["duration"] = min(cur, slot)
        it = _update(aid, upd)
    if str(body.get("clientId") or "").strip():
        it = _update(aid, lambda x: x.update(clientId=str(body["clientId"]).strip(), clientName=str(body.get("clientName") or "")[:200],
                                             isPotential=bool(body.get("isPotential")), clientAuto=False))
    it = await asyncio.to_thread(_ensure_client, aid)
    res = await asyncio.to_thread(push_crm, it)
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
            "termsManagementId": int(c.get("termsManagementId") or 0),
            "defaultClientId": int(c.get("defaultClientId") or 0), "sendEmail": bool(c.get("sendEmail", False)),
            "salesIds": {str(k): int(v) for k, v in (c.get("salesIds") or {}).items() if str(v).strip().isdigit()}}


def save_crm_settings(c: dict) -> dict:
    cur = crm_settings()
    for k in ("url", "authHeader", "authPrefix"):
        if k in c:
            cur[k] = str(c[k] or "").strip()[:500]
    for k in ("duration", "termsManagementId", "defaultClientId"):
        if k in c:
            cur[k] = int(c[k] or 0)
    if "sendEmail" in c:
        cur["sendEmail"] = bool(c["sendEmail"])
    if "salesIds" in c:
        cur["salesIds"] = {str(k): int(v) for k, v in (c["salesIds"] or {}).items() if str(v).strip().isdigit()}
    if isinstance(c.get("repNames"), dict):     # اسم الموظف عندنا = اسمه في الـ CRM
        names = {str(k): str(v).strip()[:80] for k, v in c["repNames"].items() if str(k).isdigit() and str(v).strip()}
        f = CTX["dir"] / "rep_names.json"
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
        old.update(names)
        f.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    if cur["url"] and not cur["url"].startswith("https://"):
        raise ValueError("رابط الـ CRM لازم يبدأ بـ https://")
    cur.pop("repNames", None)
    (CTX["dir"] / "crm.json").write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def crm_configured() -> bool:
    return bool(crm_settings()["url"])


def _add_minutes(hhmm: str, mins: int) -> str:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    t = h * 60 + m + mins
    return f"{(t // 60) % 24:02d}:{t % 60:02d}"


def _client_id(it: dict, cfg: dict) -> int:
    v = str(it.get("clientId") or "").strip()
    return int(v) if v.isdigit() and int(v) > 0 else int(cfg.get("defaultClientId") or 0)


def _find_slots(obj) -> list:
    """الفترات المتاحة من رد الـ CRM (availableSlots بس — مش occupiedSlots ولا busyEmployees)"""
    if not isinstance(obj, dict):
        return []
    for key in ("availability", "data"):
        if isinstance(obj.get(key), dict) and "availableSlots" not in obj:
            r = _find_slots(obj[key])
            if r:
                return r
    out = []
    for x in obj.get("availableSlots") or []:
        if isinstance(x, dict):
            f, t = str(x.get("timeFrom") or "")[:5], str(x.get("timeTo") or "")[:5]
            if f:
                out.append({"timeFrom": f, "timeTo": t})
    return sorted(out, key=lambda x: x["timeFrom"])


# ------------------------------------------------------------------ CRM client (قراءة من الـ API)
TOKEN = {"value": "", "exp": None}


def _base(cfg: dict | None = None) -> str:
    from urllib.parse import urlsplit
    u = urlsplit((cfg or crm_settings())["url"])
    return f"{u.scheme}://{u.netloc}"


def _sign_in() -> str:
    """لو اليوزر والباسورد متسجلين (sudo nq-setkey crm-login) بنجيب توكن جديد لوحدنا"""
    user, pw, db = (os.environ.get(k, "").strip() for k in ("NQ_CRM_USER", "NQ_CRM_PASS", "NQ_CRM_DB"))
    if not (user and pw):
        return ""
    import requests
    r = requests.post(_base() + "/api/v1/authentication/sign-in", timeout=20,
                      headers={"Content-Type": "application/json; ver=1.0", "Accept": "application/json"},
                      data=json.dumps({"username": user, "password": pw, "companyDB": db}))
    j = r.json() if r.content else {}
    d = j.get("data") if isinstance(j.get("data"), dict) else j
    tok = d.get("accessToken") or ""
    if tok:
        TOKEN["value"] = tok
        try:
            TOKEN["exp"] = dt.datetime.fromisoformat(str(d.get("accessTokenExpiresAt")).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            TOKEN["exp"] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    return tok


def _token(force: bool = False) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    if TOKEN["value"] and not force and (not TOKEN["exp"] or TOKEN["exp"].tzinfo is None or TOKEN["exp"] > now + dt.timedelta(minutes=2)):
        return TOKEN["value"]
    if force or os.environ.get("NQ_CRM_USER"):
        try:
            t = _sign_in()
            if t:
                return t
        except Exception as e:  # noqa: BLE001
            print("crm sign-in failed:", e, flush=True)
    return os.environ.get("NQ_CRM_TOKEN", "").strip()


def crm_headers(cfg: dict | None = None, force: bool = False) -> dict:
    cfg = cfg or crm_settings()
    h = {"Content-Type": "application/json; ver=1.0", "Accept": "application/json; ver=1.0, application/json"}
    tok = _token(force)
    if tok:
        pfx = (cfg["authPrefix"] or "").strip()
        tok = tok.removeprefix("Bearer ").removeprefix("bearer ").strip()
        h[(cfg["authHeader"] or "Authorization").strip()] = f"{pfx} {tok}" if pfx else tok
    return h


def crm_request(method: str, path: str, params: dict | None = None, body=None):
    """طلب للـ CRM — لو رجع 401 وعندنا يوزر وباسورد بنعمل sign-in ونعيد مرة"""
    import requests
    cfg = crm_settings()
    url = path if path.startswith("http") else _base(cfg) + path
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = requests.request(method, url, params=params, data=data, headers=crm_headers(cfg), timeout=30)
    if r.status_code == 401 and os.environ.get("NQ_CRM_USER"):
        r = requests.request(method, url, params=params, data=data, headers=crm_headers(cfg, force=True), timeout=30)
    return r


def _get_json(path: str, params: dict | None = None):
    r = crm_request("GET", path, params)
    if r.status_code == 401:
        raise HTTPException(502, "الـ CRM طالب تسجيل دخول — حط التوكن بـ sudo nq-setkey crm أو اليوزر بـ sudo nq-setkey crm-login")
    if r.status_code >= 400:
        raise HTTPException(502, f"الـ CRM رجّع {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "رد الـ CRM مش JSON")


def _items(j) -> list:
    """الـ CRM ممكن يرجّع list على طول، أو {data:[...]}، أو {data:{items:[...]}}"""
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("data", "items", "result", "value", "records"):
            v = j.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                r = _items(v)
                if r:
                    return r
    return []


def _digits(s) -> str:
    d = "".join(c for c in str(s or "") if c.isdigit())
    return d[-9:]          # آخر 9 أرقام: بيتجاهل 966/0/+ في الأول


def search_customers(q: str, limit: int = 20) -> list:
    out = []
    try:
        for c in _items(_get_json("/api/v1/customers", {"q": q, "PageNumber": 1, "PageSize": limit})):
            out.append({"id": c.get("id"), "name": c.get("name") or c.get("arName") or c.get("enName") or "",
                        "phone": c.get("phoneNumber") or c.get("mobile") or "", "code": c.get("code") or "",
                        "potential": False})
    except HTTPException:
        if not out:
            raise
    try:
        for c in _items(_get_json("/api/v1/customers-potential", {"q": q, "PageNumber": 1, "PageSize": limit})):
            out.append({"id": c.get("customersPotentialId") or c.get("id"),
                        "name": c.get("customerName") or c.get("name") or "",
                        "phone": c.get("phoneNumber") or c.get("mobile") or c.get("phone") or "",
                        "code": c.get("code") or "", "potential": True})
    except HTTPException:
        pass
    return [x for x in out if x["id"]]


def match_client(number: str) -> dict | None:
    """بيدوّر على العميل في الـ CRM برقم تليفونه"""
    d = _digits(number)
    if len(d) < 7:
        return None
    try:
        for c in search_customers(d):
            if _digits(c["phone"]) == d:
                return c
    except Exception as e:  # noqa: BLE001
        print("crm match failed:", e, flush=True)
    return None


@router.get("/api/crm/customers")
def crm_customers(q: str):
    q = q.strip()
    if len(q) < 2:
        return {"items": []}
    if sum(c.isdigit() for c in q) >= 7:
        q = _digits(q)
    return {"items": search_customers(q)}


@router.get("/api/crm/employees")
def crm_employees(q: str = ""):
    seen, out = set(), []
    for path, params in (("/api/v1/sales-people", {"q": q, "PageSize": 500}),
                         ("/api/v1/employees", {"q": q, "IsSales": "true", "PageSize": 500})):
        try:
            for e in _items(_get_json(path, params)):
                eid = e.get("id") or e.get("employeeId")
                if eid and eid not in seen:
                    seen.add(eid)
                    out.append({"id": eid, "name": e.get("name") or e.get("arName") or e.get("employeeName") or ""})
        except HTTPException:
            if path.endswith("employees") and not out:
                raise
        if out:
            break
    return {"items": out}


@router.get("/api/crm/terms")
def crm_terms():
    return {"items": [{"id": t.get("id"), "name": t.get("name") or t.get("arName") or t.get("title") or str(t.get("id"))}
                      for t in _items(_get_json("/api/v1/terms-managements", {"PageSize": 200})) if t.get("id")]}


@router.get("/api/crm/availability/{aid}")
def crm_availability(aid: str, date: str = "", time: str = ""):
    """المواعيد الفاضية للمندوب في اليوم ده — قبل ما نأكد"""
    it = next((x for x in _load() if x["id"] == aid), None)
    if not it:
        raise HTTPException(404, "مش موجود")
    cfg = crm_settings()
    sid = cfg["salesIds"].get(str(it.get("ext")), 0)
    if not sid:
        raise HTTPException(400, f"رقم الموظف ({it.get('repName') or it.get('ext')}) في الـ CRM مش متحدد — من تابة ⚙️ الإعدادات ← الربط مع الـ CRM")
    date = (date or it.get("date") or "")[:10]
    if not date:
        raise HTTPException(400, "حدد التاريخ الأول")
    start = (time or it.get("time") or "10:00")[:5]
    dur = int(it.get("duration") or cfg["duration"] or 60)
    j = _get_json("/api/v1/crm-appointments/employees-availability",
                  {"EmployeeIds": sid, "Date": date, "TimeFrom": start, "TimeTo": _add_minutes(start, dur)})
    d = j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), dict) else (j if isinstance(j, dict) else {})
    hours = [{"from": str(w.get("startTime") or "")[:5], "to": str(w.get("endTime") or "")[:5]}
             for w in d.get("workingHours") or [] if isinstance(w, dict)]
    busy = [{"from": str(b.get("timeFrom") or "")[:5], "to": str(b.get("timeTo") or "")[:5],
             "code": b.get("appointmentCode") or "", "reason": b.get("reason") or ""}
            for b in d.get("busyEmployees") or [] if isinstance(b, dict)]
    return {"date": date, "timeFrom": start, "duration": dur, "isAvailable": d.get("isAvailable"),
            "slots": _find_slots(d), "busy": busy, "workingHours": hours, "message": j.get("message") if isinstance(j, dict) else ""}


def crm_body(it: dict, cfg: dict) -> dict:
    """شكل الطلب زي الـ Swagger بتاع الـ CRM (Meeting)"""
    dur = int(it.get("duration") or cfg["duration"] or 60)
    start = (it.get("time") or "10:00")[:5]
    place = (it.get("location") or "").strip()
    online = place.lower().startswith("http") or it.get("kind") in ("ديمو", "مكالمة") and not place
    sales_id = cfg["salesIds"].get(str(it.get("ext")), 0)
    return {
        "id": 0, "code": "", "clientId": _client_id(it, cfg),
        "date": f"{it['date']}T00:00:00", "timeFrom": start, "timeTo": _add_minutes(start, dur), "duration": dur,
        "objectives": " — ".join(x for x in (it.get("kind"), it.get("details")) if x) or "متابعة مع العميل",
        "requirement": it.get("quote") or "", "results": "",
        "employeeIds": [sales_id] if sales_id else [],
        "isPotential": bool(it.get("isPotential")), "termsManagementId": cfg["termsManagementId"],
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
    body = crm_body(it, cfg)
    # نقول السبب بالعربي قبل ما الـ CRM يرفض
    missing = []
    if not body["salesId"]:
        missing.append(f"رقم الموظف ({it.get('repName') or it.get('ext')}) في الـ CRM — اكتبه في تابة ⚙️ الإعدادات ← الربط مع الـ CRM")
    if not body["clientId"]:
        missing.append("رقم العميل في الـ CRM (Client ID) — اكتبه في الطلب، أو حط عميل افتراضي في الإعدادات")
    if missing:
        return {"ok": False, "code": "ناقص", "at": now, "msg": json.dumps({"message": " • ".join(missing)}, ensure_ascii=False)}
    try:
        r = crm_request("POST", cfg["url"], body=body)
        res = {"ok": r.status_code < 300, "code": r.status_code, "at": now, "msg": r.text[:4000]}
        if r.status_code == 401:
            res["msg"] = "الـ CRM طالب تسجيل دخول — التوكن خلص أو غلط: sudo nq-setkey crm (أو sudo nq-setkey crm-login عشان يتجدد لوحده)"
        try:
            j = r.json()
            if isinstance(j, dict):
                if j.get("succeeded") is False:     # الـ CRM بيرجّع 200 أحيانًا ومعاه succeeded=false
                    res["ok"] = False
                data = j.get("data") if isinstance(j.get("data"), dict) else {}
                res["crmId"] = j.get("id") or data.get("id") or None
                if j.get("message"):
                    res["text"] = str(j["message"])[:300]
                alts = _find_slots(j)
                if alts:
                    res["alternatives"] = alts[:12]
        except Exception:  # noqa: BLE001
            pass
        return res
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "at": now, "msg": str(e)[:300]}


@router.get("/api/crm/settings")
def get_crm():
    c = crm_settings()
    return {**c, "hasToken": bool(os.environ.get("NQ_CRM_TOKEN", "").strip()),
            "hasLogin": bool(os.environ.get("NQ_CRM_USER", "").strip() and os.environ.get("NQ_CRM_PASS", "").strip())}


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
