"""حسابات الدخول للمناديب + شاشة "أدائي" في التطبيق.

- المدير بيحدد لكل تحويلة اسم مستخدم وكلمة سر من شاشة الموظفين في اللوحة.
- المندوب بيدخل التطبيق باسم المستخدم وكلمة السر، والسيرفر بيرجّع له بيانات التحويلة.
  المندوب عمره ما بيعرف باسورد التحويلة الحقيقي، ولو اتشال اسم المستخدم مفيش دخول بالتحويلة.
- /app/me بيرجّع للمندوب: مكالماته وتحليلها، أخطاءه، نقاط القوة والضعف، الدروس،
  طلبات نقل العملاء منه وإليه، وطلبات المواعيد بتاعته."""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
FAILS: dict = {}
# بتتظبط من app.py
CTX = {"dir": Path("/var/lib/nq-calls"), "sip_secret": None, "check_ext": None, "names": lambda: {},
       "analysis": None, "calls": None, "rubric": lambda: [], "lessons": lambda: {},
       "appointments": lambda: [], "conflicts": lambda: [], "training": lambda e: None, "tz": dt.timezone(dt.timedelta(hours=3))}


# ------------------------------------------------------------------ تخزين الحسابات
def _file() -> Path:
    return CTX["dir"] / "app_users.json"


def _users() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(u: dict):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(u, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)
    try:
        f.chmod(0o600)
    except OSError:
        pass


def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()


def _norm(u: str) -> str:
    return str(u or "").strip().lower()


@router.get("/api/logins")
def get_logins():
    return {ext: {"username": v["username"], "hasPassword": bool(v.get("hash"))} for ext, v in _users().items()}


@router.put("/api/logins")
async def put_logins(request: Request):
    """{"ext": {"username": "...", "password": "..."(اختياري)}} — username فاضي = إلغاء الحساب"""
    body = await request.json()
    with LOCK:
        cur = _users()
        for ext, v in (body or {}).items():
            ext = str(ext)
            if not ext.isdigit():
                continue
            user = str((v or {}).get("username") or "").strip()
            pw = str((v or {}).get("password") or "")
            if not user:
                cur.pop(ext, None)
                continue
            if not re.fullmatch(r"[\w.\-@]{3,40}", user):
                raise HTTPException(400, f"اسم المستخدم للتحويلة {ext}: من 3 لـ 40 حرف أو رقم، من غير مسافات")
            if user.isdigit():
                raise HTTPException(400, f"اسم المستخدم للتحويلة {ext} لازم يبقى فيه حروف، مش أرقام بس")
            rec = cur.get(ext) or {}
            rec["username"] = user
            if pw:
                if len(pw) < 6:
                    raise HTTPException(400, f"كلمة السر للتحويلة {ext} لازم 6 حروف على الأقل")
                rec["salt"] = secrets.token_hex(16)
                rec["hash"] = _hash(pw, rec["salt"])
                rec["changed"] = dt.datetime.now(dt.timezone.utc).isoformat()
            if not rec.get("hash"):
                raise HTTPException(400, f"اكتب كلمة سر للتحويلة {ext}")
            cur[ext] = rec
        # أي تحويلة مش في الطلب بتفضل زي ما هي — واسم المستخدم مايتكررش على كل الحسابات
        owner = {}
        for e, v in cur.items():
            k = _norm(v.get("username"))
            if k in owner:
                raise HTTPException(400, f"اسم المستخدم \"{v['username']}\" مستخدم للتحويلة {owner[k]}")
            owner[k] = e
        _save(cur)
    return get_logins()


# ------------------------------------------------------------------ الدخول من التطبيق
@router.post("/app/login")
async def app_login(request: Request):
    body = await request.json()
    user = str(body.get("username") or "").strip()
    pw = str(body.get("password") or "")
    key = _norm(user)
    now = time.time()
    FAILS[key] = [t for t in FAILS.get(key, []) if now - t < 900]
    if len(FAILS[key]) >= 8:
        raise HTTPException(429, "محاولات كتير غلط — استنى ربع ساعة وجرّب تاني")
    users = _users()
    ext = next((e for e, v in users.items() if _norm(v.get("username")) == key), None)
    ok = False
    if ext:
        rec = users[ext]
        ok = bool(rec.get("hash")) and hmac.compare_digest(_hash(pw, rec["salt"]), rec["hash"])
    elif user.isdigit():
        # تحويلة من غير حساب: الدخول القديم برقمها وباسوردها لسه شغال
        if user in users:
            ok = False
        else:
            ext = user
            ok = CTX["check_ext"](user, pw)
    if not ok:
        FAILS[key].append(now)
        if user.isdigit() and user in users:
            raise HTTPException(401, "التحويلة دي ليها اسم مستخدم — ادخل بيه")
        raise HTTPException(401, "اسم المستخدم أو كلمة السر غلط")
    secret = CTX["sip_secret"](ext)
    if not secret:
        raise HTTPException(404, "التحويلة مش موجودة في السنترال")
    return JSONResponse({"ext": ext, "password": secret, "username": users.get(ext, {}).get("username", ext),
                         "name": CTX["names"]().get(ext, "")}, headers=HDR)


@router.post("/app/password")
async def app_password(request: Request):
    """المندوب يغيّر كلمة سر حسابه من التطبيق"""
    body = await request.json()
    ext, sip = str(body.get("ext") or ""), str(body.get("password") or "")
    if not CTX["check_ext"](ext, sip):
        raise HTTPException(401, "سجّل دخول تاني")
    old, new = str(body.get("old") or ""), str(body.get("new") or "")
    with LOCK:
        users = _users()
        rec = users.get(ext)
        if not rec:
            raise HTTPException(400, "حسابك لسه مالوش اسم مستخدم — اطلب من المدير يعمله")
        if not hmac.compare_digest(_hash(old, rec["salt"]), rec["hash"]):
            raise HTTPException(400, "كلمة السر الحالية غلط")
        if len(new) < 6:
            raise HTTPException(400, "كلمة السر الجديدة لازم 6 حروف على الأقل")
        rec["salt"] = secrets.token_hex(16)
        rec["hash"] = _hash(new, rec["salt"])
        rec["changed"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _save(users)
    return JSONResponse({"ok": True}, headers=HDR)


# ------------------------------------------------------------------ شاشة "أدائي"
def _range(period: str):
    tz = CTX["tz"]
    today = dt.datetime.now(tz).date()
    if period == "week":
        return today - dt.timedelta(days=6), today
    if period == "month":
        return today.replace(day=1), today
    if period == "quarter":
        return today - dt.timedelta(days=89), today
    return today, today


def _top(counter: Counter, n=6):
    return [{"text": k, "count": v} for k, v in counter.most_common(n)]


@router.post("/app/me")
async def me(request: Request):
    body = await request.json()
    ext, pw = str(body.get("ext") or ""), str(body.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        raise HTTPException(401, "سجّل دخول تاني")
    d1, d2 = _range(str(body.get("period") or "week"))
    all_calls = [c for c in CTX["calls"](d1, d2) if str(c["ext"]) == ext]
    rows = [r for r in CTX["analysis"](d1, d2) if str(r["call"]["ext"]) == ext]
    rubric = {r["key"]: r["name"] for r in CTX["rubric"]()}

    strengths, weaknesses, issues, outcomes = Counter(), Counter(), Counter(), Counter()
    issue_ex: dict = {}
    crit: dict = {}
    scores, tips, lessons, recent = [], [], [], []
    for r in sorted(rows, key=lambda x: x["call"]["time"], reverse=True):
        a, c = r["analysis"] or {}, r["call"]
        if isinstance(a.get("agent_score"), (int, float)):
            scores.append(a["agent_score"])
        if a.get("outcome"):
            outcomes[a["outcome"]] += 1
        for s in a.get("strengths") or []:
            strengths[str(s).strip()] += 1
        for s in a.get("weaknesses") or []:
            weaknesses[str(s).strip()] += 1
        for i in a.get("agent_issues") or []:
            t = i.get("type") if isinstance(i, dict) else str(i)
            if t:
                issues[t] += 1
                if isinstance(i, dict) and i.get("detail"):
                    issue_ex.setdefault(t, []).append(i["detail"])
        for cr in a.get("criteria") or []:
            k = cr.get("key")
            try:
                crit.setdefault(k, []).append(float(cr.get("score")))
            except (TypeError, ValueError):
                pass
        if a.get("coaching_tip"):
            tips.append({"text": a["coaching_tip"], "time": c["time"], "number": c["number"]})
        if a.get("lesson"):
            lessons.append({"text": a["lesson"], "time": c["time"], "number": c["number"]})
        recent.append({"id": c["id"], "time": c["time"], "number": c["number"], "dir": c.get("dir"),
                       "customer": (c.get("note") or {}).get("customer") or "",
                       "score": a.get("agent_score"), "outcome": a.get("outcome") or "",
                       "summary": a.get("summary") or "", "tip": a.get("coaching_tip") or ""})

    # درس المدير للموظف ده من "الدروس المستفادة" (آخر فترة اتعملت)
    focus = None
    for key, v in sorted((CTX["lessons"]() or {}).items(), reverse=True):
        hit = next((x for x in (v or {}).get("by_rep") or [] if str(x.get("ext")) == ext), None)
        if hit:
            focus = {"focus": hit.get("focus", ""), "training": hit.get("training", ""), "period": key.replace("|", " → ")}
            break

    names = CTX["names"]()
    conf = []
    for x in CTX["conflicts"]():
        mine, against = str(x.get("newExt")) == ext, str(x.get("firstExt")) == ext
        if not (mine or against):
            continue
        conf.append({"id": x["id"], "side": "mine" if mine else "against", "status": x["status"],
                     "customer": x.get("customer") or "", "number": x.get("number") or "",
                     "other": names.get(str(x["firstExt"] if mine else x["newExt"]), ""),
                     "otherExt": str(x["firstExt"] if mine else x["newExt"]),
                     "reason": x.get("reason") or "", "requestedAt": x.get("requestedAt"),
                     "decidedAt": x.get("decidedAt")})
    conf.sort(key=lambda v: (v["status"] != "pending", -(_ts(v["decidedAt"] or v["requestedAt"]))))

    appts = []
    for it in CTX["appointments"]():
        if str(it.get("ext")) != ext:
            continue
        crm = it.get("crm") or {}
        appts.append({"id": it["id"], "status": it["status"], "customer": it.get("customer") or "",
                      "number": it.get("number") or "", "date": it.get("date"), "time": it.get("time"),
                      "kind": it.get("kind") or "", "created": it.get("created"),
                      "crm": {"ok": crm.get("ok"), "msg": (crm.get("msg") or "")[:200], "pending": crm.get("pending")} if crm else None})
    appts.sort(key=lambda v: (v["status"] != "pending", v.get("date") or "", v.get("time") or ""), reverse=False)

    answered = [c for c in all_calls if c.get("status") == "تم الرد"]
    return JSONResponse({
        "period": {"from": str(d1), "to": str(d2)},
        "summary": {"calls": len(all_calls), "answered": len(answered),
                    "talkSec": sum(int(c.get("talkSec") or 0) for c in all_calls),
                    "analyzed": len(rows), "avgScore": round(sum(scores) / len(scores)) if scores else None,
                    "outcomes": dict(outcomes)},
        "criteria": [{"key": k, "name": rubric.get(k, k), "avg": round(sum(v) / len(v), 1)}
                     for k, v in crit.items() if v],
        "strengths": _top(strengths), "weaknesses": _top(weaknesses),
        "issues": [{"type": t, "count": n, "examples": issue_ex.get(t, [])[:3]} for t, n in issues.most_common(8)],
        "tips": tips[:6], "lessons": lessons[:6], "focus": focus, "training": CTX["training"](ext),
        "calls": recent[:25],
        "conflicts": conf[:30],
        "appointments": appts[:40],
    }, headers=HDR)


def _ts(s) -> float:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0
