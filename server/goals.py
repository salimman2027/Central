"""الأهداف: لكل مندوب أهداف (افتراضية للكل، وممكن تختلف لكل فرقة)، والمحقق بيتحسب:
- المكالمات والمكالمات اللي اتردت: من السنترال (هدف يومي × أيام الشغل في الفترة)
- الاجتماعات: من طلبات المواعيد (هدف شهري)
- العملاء المحققين والمبيعات: المدير بيدخّلهم لكل مندوب في الشهر (هدف شهري / ربع سنوي)
قائد الفرقة = مجموع أهداف فريقه ومحققهم. أيام الشغل: الأحد للخميس."""
import datetime as dt
import json
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
CTX = {"dir": Path("/var/lib/nq-calls"), "tz": dt.timezone(dt.timedelta(hours=3)), "calls": None,
       "appointments": lambda: [], "teams": lambda: {}, "names": lambda: {}, "reps": lambda: [],
       "scope": lambda: None, "check_ext": lambda e, p: False}
METRICS = [  # المفتاح، الاسم، وحدة الهدف، المصدر
    {"k": "calls", "name": "مكالمات", "per": "day", "unit": "مكالمة"},
    {"k": "answered", "name": "مكالمات اترد عليها", "per": "day", "unit": "مكالمة"},
    {"k": "meetings", "name": "اجتماعات", "per": "month", "unit": "اجتماع"},
    {"k": "closed", "name": "عملاء محققين", "per": "month", "unit": "عميل", "manual": True},
    {"k": "sales", "name": "مبيعات", "per": "quarter", "unit": "ريال", "manual": True},
]
DEFAULTS = {"calls": 100, "answered": 50, "meetings": 20, "closed": 5, "sales": 600000}
WORKDAYS = {6, 0, 1, 2, 3}   # Python weekday: الأحد=6 … الخميس=3


def _f() -> Path:
    return CTX["dir"] / "goals.json"


def load() -> dict:
    try:
        d = json.loads(_f().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("defaults", dict(DEFAULTS))
    d.setdefault("teams", {})
    d.setdefault("manual", {})
    return d


def _save(d: dict):
    tmp = _f().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_f())


def today() -> dt.date:
    return dt.datetime.now(CTX["tz"]).date()


def period_range(kind: str, ref: dt.date) -> tuple:
    if kind == "day":
        return ref, ref
    if kind == "month":
        a = ref.replace(day=1)
        b = (a + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        return a, b
    q = (ref.month - 1) // 3
    a = dt.date(ref.year, q * 3 + 1, 1)
    b = (dt.date(ref.year + (q == 3), (q * 3 + 3) % 12 + 1, 1)) - dt.timedelta(days=1)
    return a, b


def workdays(a: dt.date, b: dt.date) -> int:
    try:   # تقويم الشغل: الأجازة الأسبوعية + الأجازات الرسمية والسنوية
        import workcal
        return workcal.workdays(a, b)
    except Exception:  # noqa: BLE001
        pass
    n, d = 0, a
    while d <= b:
        n += d.weekday() in WORKDAYS
        d += dt.timedelta(days=1)
    return n


def months_in(a: dt.date, b: dt.date) -> list:
    out, d = [], a.replace(day=1)
    while d <= b:
        out.append(d.strftime("%Y-%m"))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return out


def targets_for(team: str, g: dict) -> dict:
    t = dict(g["defaults"])
    t.update({k: v for k, v in (g["teams"].get(team) or {}).items() if isinstance(v, (int, float)) and v >= 0})
    return t


def scale(metric: dict, val: float, a: dt.date, b: dt.date) -> float:
    """هدف الفترة: اليومي × أيام الشغل، الشهري × عدد الشهور، الربع سنوي ÷ 3 لكل شهر"""
    if metric["per"] == "day":
        return val * workdays(a, b)
    if metric["per"] == "month":
        return val * len(months_in(a, b))
    return val * len(months_in(a, b)) / 3


def progress(kind: str = "month", ref: dt.date | None = None) -> dict:
    ref = ref or today()
    a, b = period_range(kind, ref)
    upto = min(b, today())
    g = load()
    tm = CTX["teams"]() or {}
    assign = {str(k): v for k, v in (tm.get("assign") or {}).items()}
    tnames = {t["id"]: t["name"] for t in tm.get("teams", [])}
    tcat = {t["id"]: t.get("category", "") for t in tm.get("teams", [])}
    names = CTX["names"]()
    sc = CTX["scope"]()
    exts = [str(r.get("id")) for r in CTX["reps"]() if str(r.get("id")).isdigit()]
    if sc is not None:
        exts = [e for e in exts if e in sc["exts"]]
    act = {e: {m["k"]: 0 for m in METRICS} for e in exts}
    for c in (CTX["calls"](a, upto) if CTX["calls"] and a <= upto else []):
        r = act.get(str(c["ext"]))
        if r is None:
            continue
        r["calls"] += 1
        r["answered"] += c.get("status") == "تم الرد"
    lo = dt.datetime.combine(a, dt.time.min, CTX["tz"])
    hi = dt.datetime.combine(b + dt.timedelta(days=1), dt.time.min, CTX["tz"])
    for x in CTX["appointments"]():
        try:
            t = dt.datetime.fromisoformat(str(x.get("created")).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=CTX["tz"])
        except (TypeError, ValueError):
            continue
        r = act.get(str(x.get("ext")))
        if r is not None and x.get("status") != "rejected" and lo <= t < hi:
            r["meetings"] += 1
    for mon in months_in(a, b):
        for e, v in (g["manual"].get(mon) or {}).items():
            if e in act:
                act[e]["closed"] += float(v.get("closed") or 0)
                act[e]["sales"] += float(v.get("sales") or 0)
    frac = 1 if kind == "day" else workdays(a, upto) / max(1, workdays(a, b))   # قد إيه عدّى من الفترة (للتوقّع)
    reps, teams = [], {}
    for e in exts:
        team = assign.get(e, "")
        tg = targets_for(team, g)
        row = {"ext": e, "name": names.get(e, e), "team": team, "teamName": tnames.get(team, ""), "category": tcat.get(team, ""),
               "m": {}}
        pcts = []
        for m in METRICS:
            if kind == "day":   # اليوم: الأهداف اليومية بس (حتى لو النهارده أجازة)
                target = tg[m["k"]] if m["per"] == "day" else 0
            else:
                target = round(scale(m, tg[m["k"]], a, b), 1)
            done = act[e][m["k"]]
            pct = round(done * 100 / target) if target else None
            fc = round(done / frac) if frac > 0 and upto < b else done
            row["m"][m["k"]] = {"target": target, "done": done, "pct": pct, "forecast": fc,
                                "forecastPct": round(fc * 100 / target) if target else None}
            if pct is not None:
                pcts.append(min(pct, 150))
        row["score"] = round(sum(pcts) / len(pcts)) if pcts else 0
        reps.append(row)
        if team:
            t = teams.setdefault(team, {"id": team, "name": tnames.get(team, team), "category": tcat.get(team, ""), "reps": 0,
                                         "m": {m["k"]: {"target": 0, "done": 0, "forecast": 0} for m in METRICS}})
            t["reps"] += 1
            for m in METRICS:
                for k in ("target", "done", "forecast"):
                    t["m"][m["k"]][k] += row["m"][m["k"]][k]
    for t in teams.values():
        ps = []
        for m in METRICS:
            x = t["m"][m["k"]]
            x["target"] = round(x["target"], 1)
            x["pct"] = round(x["done"] * 100 / x["target"]) if x["target"] else None
            x["forecastPct"] = round(x["forecast"] * 100 / x["target"]) if x["target"] else None
            if x["pct"] is not None:
                ps.append(min(x["pct"], 150))
        t["score"] = round(sum(ps) / len(ps)) if ps else 0
    reps.sort(key=lambda r: -r["score"])
    return {"period": kind, "from": str(a), "to": str(b), "upto": str(upto), "elapsed": round(frac * 100),
            "workdays": workdays(a, b), "workdaysDone": workdays(a, upto), "metrics": METRICS,
            "reps": reps, "teams": sorted(teams.values(), key=lambda t: -t["score"]), "months": months_in(a, b)}


# ------------------------------------------------------------------ اللوحة
@router.get("/api/goals/progress")
def api_progress(period: str = "month", date: str = ""):
    if period not in ("day", "month", "quarter"):
        raise HTTPException(400, "الفترة غلط")
    ref = dt.date.fromisoformat(date) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or "") else None
    return progress(period, ref)


@router.get("/api/goals/settings")
def get_settings():
    g = load()
    return {"defaults": g["defaults"], "teams": g["teams"], "metrics": METRICS}


@router.put("/api/goals/settings")
async def put_settings(request: Request):
    b = await request.json()
    keys = {m["k"] for m in METRICS}

    def clean(d):
        out = {}
        for k, v in (d or {}).items():
            if k in keys and v not in ("", None):
                try:
                    out[k] = max(0, float(v))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"قيمة غلط في {k}")
        return out
    with LOCK:
        g = load()
        if "defaults" in b:
            g["defaults"] = {**DEFAULTS, **clean(b["defaults"])}
        if "teams" in b:
            g["teams"] = {str(t): clean(v) for t, v in (b["teams"] or {}).items() if clean(v)}
        _save(g)
    return get_settings()


@router.put("/api/goals/manual")
async def put_manual(request: Request):
    """المدير يدخّل المبيعات والعملاء المحققين لمندوب في شهر"""
    b = await request.json()
    mon, ext = str(b.get("month") or ""), str(b.get("ext") or "")
    if not re.fullmatch(r"\d{4}-\d{2}", mon) or not ext.isdigit():
        raise HTTPException(400, "الشهر أو التحويلة غلط")
    with LOCK:
        g = load()
        rec = g["manual"].setdefault(mon, {}).setdefault(ext, {})
        for k in ("sales", "closed"):
            if k in b:
                try:
                    rec[k] = max(0, float(b[k] or 0))
                except (TypeError, ValueError):
                    raise HTTPException(400, "رقم غلط")
        _save(g)
    return {"ok": True, "month": mon, "ext": ext, **rec}


@router.get("/api/goals/manual")
def get_manual(month: str = ""):
    mon = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else today().strftime("%Y-%m")
    return {"month": mon, "items": load()["manual"].get(mon, {})}


# ------------------------------------------------------------------ التطبيق (المندوب)
@router.post("/app/goals")
async def app_goals(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not ext.isdigit() or not CTX["check_ext"](ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    out = {}
    for kind in ("day", "month", "quarter"):
        p = progress(kind)
        me = next((r for r in p["reps"] if r["ext"] == ext), None)
        team = None
        if me and me["team"]:
            team = next((t for t in p["teams"] if t["id"] == me["team"]), None)
        out[kind] = {"from": p["from"], "to": p["to"], "elapsed": p["elapsed"], "me": me, "team": team}
    return JSONResponse({"metrics": METRICS, **out}, headers=HDR)
