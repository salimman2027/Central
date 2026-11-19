"""مواعيد المهندسين من الـ CRM للمناديب (بصلاحية):
المدير بيختار المهندس (أي موظف في الـ CRM) ويحدد مين من المناديب يشوفه، وهل يشوف تفاصيل الميعاد
(الكود والسبب) ولا "محجوز / فاضي" بس. المندوب بيشوف اليوم: ساعات العمل، المحجوز، والفاضي."""
import datetime as dt
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
HDR = {"Access-Control-Allow-Origin": "*"}
CTX = {"dir": Path("/var/lib/nq-calls"), "check_ext": lambda e, p: False, "get_json": None, "items": None,
       "names": lambda: {}, "tz": dt.timezone(dt.timedelta(hours=3))}
_CACHE: dict = {}


def _f() -> Path:
    return CTX["dir"] / "engineers.json"


def load() -> list:
    try:
        return json.loads(_f().read_text(encoding="utf-8")).get("engineers", [])
    except Exception:  # noqa: BLE001
        return []


def visible(ext: str) -> list:
    return [e for e in load() if "*" in e.get("viewers", []) or str(ext) in e.get("viewers", [])]


def _hm(s) -> int:
    m = re.match(r"(\d{1,2}):(\d{2})", str(s or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else -1


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def day(eid: int, date: str, details: bool) -> dict:
    """يوم المهندس من الـ CRM: ساعات العمل + المحجوز + الفاضي (ساعات العمل ناقص المحجوز)"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise HTTPException(400, "التاريخ غلط")
    key = (eid, date)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 90:
        d = hit[1]
    else:
        j = CTX["get_json"]("/api/v1/crm-appointments/employees-availability",
                            {"EmployeeIds": eid, "Date": date, "TimeFrom": "06:00", "TimeTo": "23:59"})
        d = j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), dict) else (j if isinstance(j, dict) else {})
        _CACHE[key] = (time.time(), d)
        if len(_CACHE) > 300:
            _CACHE.clear()
    hours = [(_hm(w.get("startTime")), _hm(w.get("endTime"))) for w in d.get("workingHours") or [] if isinstance(w, dict)]
    hours = [(a, b) for a, b in hours if 0 <= a < b]
    busy = []
    for b in d.get("busyEmployees") or []:
        if not isinstance(b, dict):
            continue
        f, t = _hm(b.get("timeFrom")), _hm(b.get("timeTo"))
        if f < 0:
            continue
        x = {"from": _fmt(f), "to": _fmt(t if t > f else f + 60)}
        if details:
            x.update(code=b.get("appointmentCode") or "", reason=b.get("reason") or b.get("objectives") or "",
                     customer=b.get("customerName") or b.get("clientName") or b.get("customerNameForMeet") or "")
        busy.append(x)
    busy.sort(key=lambda x: x["from"])
    free = []
    for a, b in hours:   # الفاضي = ساعات العمل ناقص المحجوز
        cur = a
        for x in busy:
            f, t = _hm(x["from"]), _hm(x["to"])
            if t <= cur or f >= b:
                continue
            if f > cur:
                free.append({"from": _fmt(cur), "to": _fmt(f)})
            cur = max(cur, t)
        if cur < b:
            free.append({"from": _fmt(cur), "to": _fmt(b)})
    free = [x for x in free if _hm(x["to"]) - _hm(x["from"]) >= 15]
    return {"date": date, "workingHours": [{"from": _fmt(a), "to": _fmt(b)} for a, b in hours], "busy": busy,
            "free": free, "off": not hours}


# ------------------------------------------------------------------ اللوحة (المدير)
@router.get("/api/eng")
def get_eng():
    return {"engineers": load()}


@router.put("/api/eng")
async def put_eng(request: Request):
    b = await request.json()
    out = []
    for e in b.get("engineers") or []:
        try:
            eid = int(e.get("id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "اختار المهندس من القايمة")
        viewers = ["*"] if "*" in (e.get("viewers") or []) else [str(v) for v in e.get("viewers") or [] if str(v).isdigit()]
        out.append({"id": eid, "name": str(e.get("name") or "").strip()[:60] or f"#{eid}", "viewers": viewers,
                    "details": bool(e.get("details"))})
    _f().write_text(json.dumps({"engineers": out}, ensure_ascii=False), encoding="utf-8")
    return {"engineers": out}


@router.get("/api/eng/people")
def eng_people(q: str = ""):
    """كل الموظفين في الـ CRM (مش المبيعات بس) عشان تختار المهندس"""
    out, seen = [], set()
    for e in CTX["items"](CTX["get_json"]("/api/v1/employees", {"q": q, "PageSize": 500})):
        eid = e.get("id") or e.get("employeeId")
        if eid and eid not in seen:
            seen.add(eid)
            out.append({"id": eid, "name": e.get("name") or e.get("arName") or e.get("employeeName") or str(eid),
                        "job": e.get("jobTitle") or e.get("position") or e.get("departmentName") or ""})
    return {"items": out}


@router.get("/api/eng/day")
def eng_day_admin(eid: int, date: str):
    return day(eid, date, True)


# ------------------------------------------------------------------ التطبيق (المندوب)
@router.post("/app/eng")
async def app_eng(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    engs = visible(ext)
    if b.get("eid") is None:
        return JSONResponse({"engineers": [{"id": e["id"], "name": e["name"]} for e in engs]}, headers=HDR)
    e = next((e for e in engs if str(e["id"]) == str(b.get("eid"))), None)
    if not e:
        return JSONResponse({"detail": "مش مسموحلك تشوف مواعيد المهندس ده"}, 403, headers=HDR)
    try:
        return JSONResponse(day(e["id"], str(b.get("date") or ""), e.get("details", False)), headers=HDR)
    except HTTPException as x:
        return JSONResponse({"detail": x.detail}, x.status_code, headers=HDR)
