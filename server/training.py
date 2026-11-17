"""خطط التدريب — 4 مستويات من تحليل المكالمات:
الشركة ككل، كل فريق، كل موظف، وقادة الفرق والمديرين.

- المدير بيدوس "اعمل خطط التدريب" لفترة معينة → السيرفر بيجمّع الأرقام (تقييمات، بنود التقييم،
  أخطاء الموظفين، أسباب عدم الشراء، نشاط المكالمات) ويبعتها لـ Claude في الخلفية.
- قائد الفريق / المدير بيشوف خطة الشركة + فرقه وموظفينه بس.
- المندوب بيشوف خطته في "أدائي" في التطبيق (آخر خطة اتعملت)."""
import datetime as dt
import json
import os
import threading
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
LOCK = threading.Lock()
JOB = {"running": False, "key": "", "error": "", "started": None}
# بتتظبط من app.py
CTX = {"dir": Path("/var/lib/nq-calls"), "analysis": None, "calls": None, "insights": None,
       "names": lambda: {}, "teams": lambda: {}, "rubric": lambda: [], "scope": lambda: None,
       "tz": dt.timezone(dt.timedelta(hours=3))}


def _file() -> Path:
    return CTX["dir"] / "training.json"


def load_all() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def latest_for_ext(ext: str):
    """خطة الموظف ده من آخر خطة اتعملت (للتطبيق)"""
    allp = load_all()
    for key in sorted(allp, key=lambda k: allp[k].get("generated", ""), reverse=True):
        hit = next((r for r in allp[key].get("reps") or [] if str(r.get("ext")) == str(ext)), None)
        if hit:
            return {**hit, "period": key.replace("|", " → "), "generated": allp[key].get("generated")}
    return None


# ------------------------------------------------------------------ الأرقام اللي بتتبعت للـ AI
def _avg(v):
    return round(sum(v) / len(v), 1) if v else None


def facts(d1: dt.date, d2: dt.date) -> dict:
    names = CTX["names"]()
    tm = CTX["teams"]() or {}
    teams = {t["id"]: t for t in tm.get("teams", [])}
    assign = {str(k): v for k, v in (tm.get("assign") or {}).items()}
    rub = {r["key"]: r["name"] for r in CTX["rubric"]()}

    reps: dict = {}

    def rep(ext):
        ext = str(ext)
        return reps.setdefault(ext, {
            "ext": ext, "name": names.get(ext, ext), "team": teams.get(assign.get(ext), {}).get("name", ""),
            "calls": 0, "answered": 0, "talk_min": 0, "analyzed": 0, "_scores": [], "_crit": {},
            "issues": Counter(), "_weak": Counter(), "_strong": Counter(), "outcomes": Counter(), "tips": []})

    for c in CTX["calls"](d1, d2):
        r = rep(c["ext"])
        r["calls"] += 1
        if c.get("status") == "تم الرد":
            r["answered"] += 1
        r["talk_min"] += round(int(c.get("talkSec") or 0) / 60)
    for x in CTX["analysis"](d1, d2):
        a, c = x["analysis"] or {}, x["call"]
        r = rep(c["ext"])
        r["analyzed"] += 1
        if isinstance(a.get("agent_score"), (int, float)):
            r["_scores"].append(a["agent_score"])
        for cr in a.get("criteria") or []:
            try:
                r["_crit"].setdefault(cr.get("key"), []).append(float(cr.get("score")))
            except (TypeError, ValueError):
                pass
        for i in a.get("agent_issues") or []:
            t = i.get("type") if isinstance(i, dict) else str(i)
            if t:
                r["issues"][t] += 1
        for s in a.get("weaknesses") or []:
            r["_weak"][str(s).strip()] += 1
        for s in a.get("strengths") or []:
            r["_strong"][str(s).strip()] += 1
        if a.get("outcome"):
            r["outcomes"][a["outcome"]] += 1
        if a.get("coaching_tip") and len(r["tips"]) < 3:
            r["tips"].append(a["coaching_tip"])

    out_reps = []
    for r in reps.values():
        if not r["calls"] and not r["analyzed"]:
            continue
        out_reps.append({
            "ext": r["ext"], "name": r["name"], "team": r["team"], "calls": r["calls"], "answered": r["answered"],
            "talk_min": r["talk_min"], "analyzed": r["analyzed"], "avg_score": _avg(r["_scores"]),
            "criteria_avg_of_10": {rub.get(k, k): _avg(v) for k, v in r["_crit"].items() if v},
            "mistakes": dict(r["issues"].most_common(6)), "weaknesses": [w for w, _ in r["_weak"].most_common(4)],
            "strengths": [s for s, _ in r["_strong"].most_common(3)], "outcomes": dict(r["outcomes"]),
            "coaching_tips": r["tips"]})

    out_teams = []
    for tid, t in teams.items():
        mem = [r for r in out_reps if assign.get(r["ext"]) == tid]
        sc = [r["avg_score"] for r in mem if r["avg_score"] is not None]
        iss = Counter()
        for r in mem:
            iss.update(r["mistakes"])
        out_teams.append({"id": tid, "name": t.get("name", tid), "leader": names.get(str(t.get("leaderExt") or ""), ""),
                          "leader_ext": str(t.get("leaderExt") or ""), "members": [f'{r["name"]} ({r["ext"]})' for r in mem],
                          "calls": sum(r["calls"] for r in mem), "avg_score": _avg(sc),
                          "score_spread": round(max(sc) - min(sc), 1) if len(sc) > 1 else None,
                          "reps_below_60": [r["name"] for r in mem if r["avg_score"] is not None and r["avg_score"] < 60],
                          "top_mistakes": dict(iss.most_common(5))})

    leaders = []
    for t in out_teams:
        u = (teams[t["id"]].get("leaderUser") or "").strip()
        if u or t["leader"]:
            leaders.append({"user": u or t["leader_ext"], "name": t["leader"] or u, "role": "leader", "teams": [t["name"]]})
    for m in tm.get("managers", []):
        leaders.append({"user": m.get("user", ""), "name": m.get("name") or m.get("user", ""), "role": "manager",
                        "teams": [teams[i]["name"] for i in m.get("teams", []) if i in teams]})

    ins = CTX["insights"](d1, d2)
    all_sc = [r["avg_score"] for r in out_reps if r["avg_score"] is not None]
    crit_all: dict = {}
    for r in out_reps:
        for k, v in r["criteria_avg_of_10"].items():
            if v is not None:
                crit_all.setdefault(k, []).append(v)
    company = {"period": f"{d1} → {d2}", "calls": sum(r["calls"] for r in out_reps),
               "answered": sum(r["answered"] for r in out_reps), "analyzed": sum(r["analyzed"] for r in out_reps),
               "avg_score": _avg(all_sc), "criteria_avg_of_10": {k: _avg(v) for k, v in crit_all.items()},
               "no_buy_reasons": [{"type": v["type"], "count": v["count"], "pct": v.get("pct"),
                                   "examples": [e["detail"] for e in v["examples"][:2]]} for v in ins["no_buy"][:6]],
               "customer_pains": [{"type": v["type"], "count": v["count"]} for v in ins["pains"][:6]],
               "call_lessons": [e["lesson"] for v in ins["no_buy"] for e in v["examples"] if e.get("lesson")][:15]}
    return {"company": company, "teams": out_teams, "reps": out_reps, "leaders": leaders}


# ------------------------------------------------------------------ الـ AI
PROMPT = """إنت مدير تدريب مبيعات خبير لشركة "الجودة الوطنية لتقنية المعلومات" (NQ) — برامج محاسبة وإدارة (ERP) في السعودية.
الفريق تيلي سيلز بيكلم عملاء سعوديين (أصحاب محلات وشركات صغيرة ومتوسطة) عشان يحدد معاهم ميعاد عرض أو يبيع.
دي أرقام تحليل المكالمات في الفترة {period} — الشركة ككل، والفرق، وكل موظف (تقييمه من 100، ومتوسط كل بند تقييم من 10،
أخطاؤه وعددها، نقاط ضعفه وقوته)، وقادة الفرق والمديرين:
<data>
{data}
</data>

اعمل خطة تدريب عملية لمدة 4 أسابيع (أسبوع العمل في السعودية من الأحد للخميس) على 4 مستويات:
1) company: خطة الشركة ككل — أكبر 3–5 فجوات مشتركة، ورش جماعية (العنوان، لمين، المدة، المحتوى)، وخطة الـ 4 أسابيع.
2) teams: لكل فريق في البيانات — فجوات الفريق، خطة 4 أسابيع، وتمارين تمثيل أدوار (role-play) بسيناريوهات من اعتراضات العملاء الحقيقية.
3) reps: لكل موظف في البيانات (بالـ ext بتاعه) — مستواه، نقاط قوته، أهم 2–3 فجوات بالأرقام، خطة أسبوعية فيها هدف وأنشطة محددة
   (يسمع مكالمات معينة، تمرين، جملة يحفظها، عدد مكالمات بهدف معين)، مؤشرات قياس (الحالي ← المستهدف)، و2–3 جمل جاهزة يقولها.
4) leaders: لكل قائد فريق ومدير في البيانات — خطة تطوير قيادته: مهارات الكوتشينج اللي محتاجها حسب أرقام فريقه
   (مثلاً لو الفرق بين أعلى وأقل موظف كبير، أو في موظفين تحت 60)، روتين ثابت (يومي/أسبوعي: مراجعة مكالمات، اجتماع 1:1،
   متابعة المؤشرات)، خطة 4 أسابيع، ومؤشرات قياس لنجاحه كقائد.

قواعد:
- اكتب بالعربي العامي المصري البسيط زي اللي في اللوحة، والجمل اللي المندوب يقولها للعميل باللهجة السعودية المهذبة.
- كل نشاط لازم يبقى محدد وقابل للتنفيذ والقياس — مش كلام عام زي "يحسّن مهارات التواصل".
- اربط كل فجوة بالرقم اللي في البيانات (مثلاً "بند الإغلاق 4.2 من 10").
- المؤشرات: الحالي من البيانات، والمستهدف واقعي خلال 4 أسابيع.
- ماتخترعش موظفين أو فرق مش موجودين في البيانات.
رجّع النتيجة عن طريق أداة save_training."""

_KPI = {"type": "array", "items": {"type": "object", "properties": {
    "name": {"type": "string"}, "now": {"type": "string"}, "target": {"type": "string"}},
    "required": ["name", "now", "target"]}}
_WEEKS = {"type": "array", "description": "4 أسابيع", "items": {"type": "object", "properties": {
    "week": {"type": "integer"}, "theme": {"type": "string", "description": "هدف/موضوع الأسبوع"},
    "activities": {"type": "array", "items": {"type": "string"}}}, "required": ["week", "theme", "activities"]}}
_STR = {"type": "array", "items": {"type": "string"}}

TOOL = {
    "name": "save_training",
    "description": "احفظ خطط التدريب للشركة والفرق والموظفين والقادة",
    "input_schema": {"type": "object", "properties": {
        "company": {"type": "object", "properties": {
            "summary": {"type": "string"}, "gaps": _STR, "kpis": _KPI, "weeks": _WEEKS,
            "workshops": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "audience": {"type": "string"}, "duration": {"type": "string"},
                "content": {"type": "string"}}, "required": ["title", "audience", "duration", "content"]}}},
            "required": ["summary", "gaps", "kpis", "weeks", "workshops"]},
        "teams": {"type": "array", "items": {"type": "object", "properties": {
            "team": {"type": "string", "description": "id الفريق زي ما هو في البيانات"},
            "summary": {"type": "string"}, "gaps": _STR, "kpis": _KPI, "weeks": _WEEKS,
            "role_plays": {"type": "array", "items": {"type": "object", "properties": {
                "scenario": {"type": "string"}, "goal": {"type": "string"}}, "required": ["scenario", "goal"]}}},
            "required": ["team", "summary", "gaps", "kpis", "weeks", "role_plays"]}},
        "reps": {"type": "array", "items": {"type": "object", "properties": {
            "ext": {"type": "string"}, "level": {"type": "string", "description": "مبتدئ / متوسط / متقدم"},
            "summary": {"type": "string"}, "strengths": _STR, "gaps": _STR, "kpis": _KPI, "weeks": _WEEKS,
            "scripts": _STR}, "required": ["ext", "level", "summary", "strengths", "gaps", "kpis", "weeks", "scripts"]}},
        "leaders": {"type": "array", "items": {"type": "object", "properties": {
            "user": {"type": "string"}, "summary": {"type": "string"}, "skills": _STR,
            "routines": {"type": "array", "items": {"type": "object", "properties": {
                "when": {"type": "string"}, "what": {"type": "string"}}, "required": ["when", "what"]}},
            "weeks": _WEEKS, "kpis": _KPI}, "required": ["user", "summary", "skills", "routines", "weeks", "kpis"]}},
    }, "required": ["company", "teams", "reps", "leaders"]},
}


def _generate(d1: dt.date, d2: dt.date, key: str):
    try:
        f = facts(d1, d2)
        if not f["company"]["analyzed"]:
            raise RuntimeError("مفيش مكالمات متحلّلة في الفترة دي — حلّل مكالمات الأول من تبويب تحليل المكالمات")
        import anthropic
        client = anthropic.Anthropic(timeout=900)
        prompt = PROMPT.format(period=f["company"]["period"], data=json.dumps(f, ensure_ascii=False, indent=1))
        msg = client.messages.create(model=os.environ.get("NQ_CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=20000,
                                     messages=[{"role": "user", "content": prompt}], tools=[TOOL],
                                     tool_choice={"type": "tool", "name": "save_training"})
        res = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if not isinstance(res, dict):
            why = " (الرد اتقطع لأنه طويل)" if getattr(msg, "stop_reason", "") == "max_tokens" else ""
            raise RuntimeError("رد Claude مش مفهوم" + why + " — جرّب تاني")
        # نكمّل الأسماء من البيانات عشان العرض
        rn = {r["ext"]: r for r in f["reps"]}
        tn = {t["id"]: t for t in f["teams"]}
        tn_by_name = {t["name"]: t for t in f["teams"]}
        ln = {x["user"]: x for x in f["leaders"]}
        for r in res.get("reps") or []:
            src = rn.get(str(r.get("ext")), {})
            r["ext"] = str(r.get("ext"))
            r.update(name=src.get("name", r["ext"]), team=src.get("team", ""), avg=src.get("avg_score"))
        for t in res.get("teams") or []:
            src = tn.get(t.get("team")) or tn_by_name.get(t.get("team")) or {}
            t.update(team=src.get("id", t.get("team")), name=src.get("name", t.get("team")), leader=src.get("leader", ""),
                     avg=src.get("avg_score"))
        for x in res.get("leaders") or []:
            src = ln.get(x.get("user")) or {}
            x.update(name=src.get("name", x.get("user")), role=src.get("role", "leader"), teams=src.get("teams", []))
        res.update(generated=dt.datetime.now(CTX["tz"]).isoformat(), calls=f["company"]["analyzed"],
                   period=f["company"]["period"])
        with LOCK:
            allp = load_all()
            allp[key] = res
            keep = sorted(allp, key=lambda k: allp[k].get("generated", ""))[-12:]
            _file().write_text(json.dumps({k: allp[k] for k in keep}, ensure_ascii=False), encoding="utf-8")
        JOB.update(error="")
    except Exception as e:  # noqa: BLE001
        JOB.update(error=str(e)[:400])
    finally:
        JOB.update(running=False)


def _filter(plan: dict, sc) -> dict:
    """قائد الفريق/المدير يشوف خطة الشركة + فرقه وموظفينه وخطته هو بس"""
    if not plan or sc is None:
        return plan
    exts, teams, user = {str(e) for e in sc["exts"]}, set(sc["teams"]), (sc.get("user") or "").lower()
    team_names = {t.get("name") for t in plan.get("teams") or [] if t.get("team") in teams}
    return {**plan,
            "teams": [t for t in plan.get("teams") or [] if t.get("team") in teams],
            "reps": [r for r in plan.get("reps") or [] if str(r.get("ext")) in exts],
            "leaders": [x for x in plan.get("leaders") or []
                        if (x.get("user") or "").lower() == user or (sc["role"] == "manager" and x.get("role") == "leader"
                                                                    and set(x.get("teams") or []) & team_names)]}


@router.get("/api/training")
def get_training(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    key = f"{date_from}|{date_to}"
    plan = load_all().get(key)
    if not plan:   # لو مفيش للفترة دي بالظبط: آخر خطة اتعملت
        allp = load_all()
        if allp:
            plan = allp[max(allp, key=lambda k: allp[k].get("generated", ""))]
    return {"plan": _filter(plan, CTX["scope"]()), "job": {**JOB, "mine": JOB["key"] == key}}


@router.post("/api/training")
def make_training(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    if CTX["scope"]() is not None:
        raise HTTPException(403, "عمل خطط التدريب للمدير بس — إنت تقدر تشوفها")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "مفتاح Claude API مش متضاف على السيرفر")
    if date_to < date_from:
        raise HTTPException(400, "التاريخ غلط")
    with LOCK:
        if JOB["running"]:
            raise HTTPException(409, "في خطة بتتعمل دلوقتي — استنى تخلص")
        key = f"{date_from}|{date_to}"
        JOB.update(running=True, key=key, error="", started=dt.datetime.now(CTX["tz"]).isoformat())
    threading.Thread(target=_generate, args=(date_from, date_to, key), daemon=True).start()
    return {"job": {**JOB, "mine": True}}
