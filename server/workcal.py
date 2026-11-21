"""تقويم الشغل: أيام الأجازة الأسبوعية، الأجازات الرسمية لكل دولة (بتتحمّل أوتوماتيك — مصر والسعودية)،
والأجازات السنوية/الخاصة بالشركة، وجدول إرسال التقرير اليومي (أيام + ساعة).
الأهداف بتحسب أيام الشغل منه، والتقرير مابيتبعتش في الأجازات."""
import datetime as dt
import json
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
LOCK = threading.Lock()
CTX = {"dir": Path("/var/lib/nq-calls"), "tz": dt.timezone(dt.timedelta(hours=3))}
COUNTRIES = {"SA": "🇸🇦 السعودية", "EG": "🇪🇬 مصر", "AE": "🇦🇪 الإمارات", "KW": "🇰🇼 الكويت", "QA": "🇶🇦 قطر",
             "BH": "🇧🇭 البحرين", "OM": "🇴🇲 عمان", "JO": "🇯🇴 الأردن"}
DAYS = ["الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت", "الحد"]   # Python weekday 0..6
DEFAULT = {"countries": ["SA", "EG"], "weekend": [4, 5],          # الجمعة والسبت
           "holidays": [], "report": {"days": [6, 0, 1, 2, 3], "time": "21:00", "skipHolidays": True, "country": "SA"}}


def _f() -> Path:
    return CTX["dir"] / "workcal.json"


def load() -> dict:
    try:
        d = json.loads(_f().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    out = {**DEFAULT, **d}
    out["report"] = {**DEFAULT["report"], **(d.get("report") or {})}
    return out


def _save(d: dict):
    tmp = _f().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_f())


def today() -> dt.date:
    return dt.datetime.now(CTX["tz"]).date()


def holiday_on(day: dt.date, country: str | None = None) -> str:
    """اسم الأجازة في اليوم ده (لو فيه) — country=None يعني أي دولة، أو "*" = أجازة الشركة كلها"""
    s = day.isoformat()
    for h in load()["holidays"]:
        if h.get("from", h.get("date")) <= s <= h.get("to", h.get("date")) and \
                (country is None or h.get("country") in ("*", country, "", None)):
            return h.get("name") or "أجازة"
    return ""


def is_workday(day: dt.date, country: str | None = None) -> bool:
    c = load()
    if day.weekday() in c["weekend"]:
        return False
    return not holiday_on(day, country or (c["countries"] or [None])[0])


def workdays(a: dt.date, b: dt.date, country: str | None = None) -> int:
    c = load()
    country = country or (c["countries"] or [None])[0]
    hol = set()
    for h in c["holidays"]:
        if h.get("country") not in ("*", country, "", None):
            continue
        x, y = dt.date.fromisoformat(h.get("from", h.get("date"))), dt.date.fromisoformat(h.get("to", h.get("date")))
        while x <= y:
            hol.add(x)
            x += dt.timedelta(days=1)
    n, d = 0, a
    while d <= b:
        n += d.weekday() not in c["weekend"] and d not in hol
        d += dt.timedelta(days=1)
    return n


def report_due(now: dt.datetime) -> tuple[bool, str]:
    """هل ميعاد التقرير جه النهارده؟ (True, "") أو (False, السبب)"""
    r = load()["report"]
    if now.weekday() not in r["days"]:
        return False, "مش من أيام التقرير"
    if r.get("skipHolidays", True):
        h = holiday_on(now.date(), r.get("country") or None)
        if h:
            return False, f"أجازة: {h}"
    hh, mm = (int(x) for x in (r.get("time") or "21:00").split(":"))
    return (now.hour, now.minute) >= (hh, mm), ""


def auto_holidays(country: str, years: list) -> list:
    """الأجازات الرسمية من مكتبة holidays (الأعياد الهجرية تقديرية — راجعها لما تتعلن رسمي)"""
    try:
        import holidays
    except ImportError:
        raise HTTPException(500, "مكتبة الأجازات مش متسطبة — شغّل sudo nq-update")
    try:
        h = holidays.country_holidays(country, years=years, language="ar")
    except Exception:  # noqa: BLE001
        h = holidays.country_holidays(country, years=years)
    out = []
    for d, name in sorted(h.items()):
        est = "تقدير" in name or "estimated" in name.lower()
        name = re.sub(r"\(تقدير[^)]*\)\s*|\(estimated\)\s*", "", name).strip()
        out.append({"date": d.isoformat(), "from": d.isoformat(), "to": d.isoformat(), "name": name, "country": country,
                    "source": "auto", "estimated": est})
    return out


# ------------------------------------------------------------------ تنبيه الأجازات (للكل — قراية بس)
@router.get("/api/calendar/upcoming")
def upcoming(days: int = 45):
    """الأجازات الجاية (اللي فيها أيام شغل) لكل الدول المختارة + أيام الشغل الباقية في الشهر"""
    c, t = load(), today()
    end = t + dt.timedelta(days=max(1, min(days, 120)))
    out = []
    for h in c["holidays"]:
        a, z = dt.date.fromisoformat(h["from"]), dt.date.fromisoformat(h.get("to") or h["from"])
        if z < t or a > end:
            continue
        lost = sum(1 for i in range((z - a).days + 1) if (a + dt.timedelta(days=i)).weekday() not in c["weekend"])
        out.append({"name": h.get("name") or "أجازة", "from": h["from"], "to": h.get("to") or h["from"],
                    "country": h.get("country") or "*", "lost": lost, "estimated": bool(h.get("estimated")),
                    "inDays": max(0, (a - t).days), "now": a <= t <= z})
    out.sort(key=lambda h: (h["from"], h["country"]))
    me = (t.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    by = {k: {"left": workdays(t, me, k), "month": workdays(t.replace(day=1), me, k)} for k in (c["countries"] or [None]) if k}
    return {"today": t.isoformat(), "countries": c["countries"], "countryNames": COUNTRIES,
            "todayOff": {k: (holiday_on(t, k) or ("أجازة أسبوعية" if t.weekday() in c["weekend"] else "")) for k in c["countries"]},
            "holidays": out, "workdays": by, "weekend": c["weekend"]}


# ------------------------------------------------------------------ API (الأدمن)
@router.get("/api/workcal")
def get_cal():
    c = load()
    return {**c, "countryNames": COUNTRIES, "dayNames": DAYS, "today": today().isoformat(),
            "todayOff": holiday_on(today(), c["report"].get("country")) or ("أجازة أسبوعية" if today().weekday() in c["weekend"] else "")}


@router.put("/api/workcal")
async def put_cal(request: Request):
    b = await request.json()
    with LOCK:
        c = load()
        if "countries" in b:
            c["countries"] = [x for x in b["countries"] if x in COUNTRIES][:6]
        if "weekend" in b:
            c["weekend"] = sorted({int(x) for x in b["weekend"] if str(x).isdigit() and 0 <= int(x) <= 6})
        if "report" in b:
            r = b["report"] or {}
            tm = str(r.get("time") or c["report"]["time"])
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", tm):
                raise HTTPException(400, "الساعة غلط — مثلاً 21:00")
            c["report"] = {"days": sorted({int(x) for x in r.get("days", c["report"]["days"]) if 0 <= int(x) <= 6}),
                           "time": tm, "skipHolidays": bool(r.get("skipHolidays", True)),
                           "country": r.get("country") if r.get("country") in COUNTRIES or r.get("country") == "" else c["report"]["country"]}
        if "holidays" in b:
            hs = []
            for h in b["holidays"] or []:
                a = str(h.get("from") or h.get("date") or "")[:10]
                z = str(h.get("to") or a)[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", z):
                    raise HTTPException(400, f"تاريخ غلط: {a}")
                if z < a:
                    a, z = z, a
                hs.append({"date": a, "from": a, "to": z, "name": str(h.get("name") or "أجازة").strip()[:80],
                           "country": h.get("country") if h.get("country") in COUNTRIES else "*",
                           "source": "auto" if h.get("source") == "auto" else "manual", "estimated": bool(h.get("estimated"))})
            c["holidays"] = sorted(hs, key=lambda x: x["from"])
        _save(c)
    return get_cal()


@router.post("/api/workcal/load")
async def load_auto(request: Request):
    """يحمّل الأجازات الرسمية لدولة (السنة دي واللي بعدها) — بيبدّل الأوتوماتيك القديم للدولة دي ويسيب اليدوي"""
    b = await request.json()
    country = str(b.get("country") or "")
    if country not in COUNTRIES:
        raise HTTPException(400, "اختار الدولة")
    y = today().year
    years = [int(x) for x in b.get("years") or [y, y + 1]]
    new = auto_holidays(country, years)
    with LOCK:
        c = load()
        c["holidays"] = sorted([h for h in c["holidays"] if not (h.get("source") == "auto" and h.get("country") == country
                                                                   and int(h["from"][:4]) in years)] + new, key=lambda x: x["from"])
        if country not in c["countries"]:
            c["countries"].append(country)
        _save(c)
    return {**get_cal(), "loaded": len(new)}
