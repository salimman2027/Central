"""تشغيل ومتابعة: تنبيهات لما حاجة تقع، المكالمات الفائتة اللي محدش رد عليها، تذكير المتابعات،
حد يومي لكل شريحة، العملاء الجدد من الـ CRM، شاشة مباشرة (مين في مكالمة + اسمع/وشوش/ادخل)،
ترتيب الموظفين (شاشة التلفزيون)، الملخص اليومي، ومتابعة خطط التدريب.

كله بيشتغل في thread واحد في الخلفية كل دقيقة (monitor_loop)."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
# بتتظبط من app.py
CTX = {"dir": Path("/var/lib/nq-calls"), "tz": dt.timezone(dt.timedelta(hours=3)), "trunk": "dinastar",
       "query": None, "calls": None, "notes": None, "names": lambda: {}, "teams": lambda: {}, "lines": lambda: {},
       "owner_of": lambda n: None, "scope": lambda: None, "check_ext": lambda e, p: False, "reps": lambda: [],
       "appointments": lambda: [], "analysis": None, "crm_get": None, "crm_on": lambda: False,
       "sales_ids": lambda: {}, "training": lambda: {}}

DEFAULTS = {"cap": 0, "capWarn": 80, "digestHour": 21, "spyExts": [], "callbackDays": 3, "leads": True, "emails": [], "emailSupervisors": True}
STATE = {"gateway": "unknown", "gatewayAt": None, "checked": None, "digestDay": "", "leadsAt": 0, "spySig": ""}


def _p(name) -> Path:
    return CTX["dir"] / name


def _jload(name, default):
    try:
        return json.loads(_p(name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _jsave(name, data):
    _p(name).parent.mkdir(parents=True, exist_ok=True)
    tmp = _p(name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_p(name))


_CC: dict = {}


def calls_c(d1: dt.date, d2: dt.date) -> list:
    """المكالمات مع كاش 30 ثانية (التطبيقات بتسأل كل دقيقة)"""
    k = (d1, d2)
    hit = _CC.get(k)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    v = CTX["calls"](d1, d2)
    if len(_CC) > 20:
        _CC.clear()
    _CC[k] = (time.time(), v)
    return v


def settings() -> dict:
    return {**DEFAULTS, **_jload("ops.json", {})}


def digits(n) -> str:
    return re.sub(r"\D", "", str(n or ""))[-9:]


def now() -> dt.datetime:
    return dt.datetime.now(CTX["tz"])


def _ts(s) -> dt.datetime:
    t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def asterisk(cmd: str, timeout=6) -> str | None:
    try:
        p = subprocess.run(["asterisk", "-rx", cmd], capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ تليجرام (التنبيهات والملخص اليومي)
def tg_ready() -> bool:
    return bool(os.environ.get("NQ_TG_TOKEN") and os.environ.get("NQ_TG_CHAT"))


def tg_send(text: str) -> bool:
    if not tg_ready():
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": os.environ["NQ_TG_CHAT"], "text": text[:4000]}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{os.environ['NQ_TG_TOKEN']}/sendMessage", data, timeout=10)
        return True
    except Exception as e:  # noqa: BLE001
        print("telegram failed:", e, flush=True)
        return False


# ------------------------------------------------------------------ الإيميل (التقرير اليومي للمديرين)
def smtp_conf() -> dict:
    """إعدادات الإيميل: من شاشة الأدمن (smtp.json) — ولو مش متظبطة هناك من nq-setkey email"""
    j = _jload("smtp.json", {})
    env = {"host": os.environ.get("NQ_SMTP_HOST", ""), "port": int(os.environ.get("NQ_SMTP_PORT") or 587),
           "user": os.environ.get("NQ_SMTP_USER", ""), "password": os.environ.get("NQ_SMTP_PASS", ""),
           "name": os.environ.get("NQ_SMTP_NAME", "NQ Call Center")}
    return {**env, **{k: v for k, v in j.items() if v not in ("", None)}} if j.get("host") else env


def mail_ready() -> bool:
    c = smtp_conf()
    return bool(c["host"] and c["user"] and c["password"])


def smtp_login(c: dict):
    import smtplib
    import ssl
    ctx = ssl.create_default_context()
    port = int(c.get("port") or 587)
    s = smtplib.SMTP_SSL(c["host"], port, timeout=20, context=ctx) if port == 465 else smtplib.SMTP(c["host"], port, timeout=20)
    if port != 465:
        s.starttls(context=ctx)
    s.login(c["user"], c["password"])
    return s


def mail_send(to: list, subject: str, html: str, text: str = "") -> str:
    """بيرجّع "" لو اتبعت أو سبب الفشل"""
    from email.message import EmailMessage
    from email.utils import formataddr
    to = [x for x in to if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x or "")]
    if not to:
        return "مفيش إيميلات"
    if not mail_ready():
        return "الإيميل مش متظبط — من الإعدادات ← 📧 إعدادات الإيميل"
    c = smtp_conf()
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, formataddr((c.get("name") or "NQ Call Center", c.get("from") or c["user"])), ", ".join(to)
    m.set_content(text or re.sub(r"<[^>]+>", "", html))
    m.add_alternative(html, subtype="html")
    try:
        with smtp_login(c) as s:
            s.send_message(m)
        return ""
    except Exception as e:  # noqa: BLE001
        print("mail failed:", e, flush=True)
        return str(e)[:200]


def digest_html(exts: set | None = None, title: str = "") -> str:
    """التقرير اليومي كإيميل (HTML): الإجمالي، الفرق، الموظفين، الأهداف، الفائتة. exts = فرق المشرف بس"""
    import html as H
    d = now().date()
    rows = [r for r in board(d, d) if exts is None or r["ext"] in exts]
    cbs = [c for c in callbacks(1) if exts is None or c["ext"] in exts]
    fu = [f for f in followups() if exts is None or f["ext"] in exts]
    tot = lambda k, rs: sum(r[k] for r in rs)  # noqa: E731
    e = H.escape
    st = "font-family:Tahoma,Arial,sans-serif;direction:rtl;text-align:right"
    td = "padding:6px 10px;border-bottom:1px solid #e5e7eb"
    kpi = lambda lbl, v: f'<td style="padding:12px;background:#f0fdfa;border-radius:10px;text-align:center"><div style="font-size:12px;color:#64748b">{lbl}</div><div style="font-size:22px;font-weight:bold;color:#0f766e">{v}</div></td>'  # noqa: E731
    out = [f'<div style="{st};max-width:760px;margin:auto;color:#0f172a">',
           f'<h2 style="margin:0 0 4px">📊 التقرير اليومي — {d:%Y-%m-%d}</h2><div style="color:#64748b;margin-bottom:14px">{e(title or "كل الفرق")}</div>',
           '<table style="width:100%;border-spacing:8px"><tr>' + kpi("مكالمات", tot("calls", rows)) + kpi("اترد عليها", tot("answered", rows))
           + kpi("دقايق كلام", tot("talkMin", rows)) + kpi("مواعيد", tot("appts", rows)) + '</tr></table>']
    tm = CTX["teams"]() or {}
    teams = [t["name"] for t in tm.get("teams", [])] + [""]
    for tn in teams:
        rs = [r for r in rows if (r["team"] or "") == tn]
        if not rs:
            continue
        out.append(f'<h3 style="margin:18px 0 6px">👥 {e(tn or "بدون فريق")} — {tot("calls", rs)} مكالمة · {tot("appts", rs)} ميعاد</h3>')
        out.append(f'<table style="width:100%;border-collapse:collapse;font-size:14px"><tr style="background:#f1f5f9"><th style="{td}">الموظف</th><th style="{td}">مكالمات</th><th style="{td}">اترد</th><th style="{td}">دقايق</th><th style="{td}">مواعيد</th><th style="{td}">التقييم</th><th style="{td}">النقط</th></tr>'
                   + "".join(f'<tr><td style="{td}"><b>{e(r["name"])}</b> <span style="color:#94a3b8">{r["ext"]}</span></td><td style="{td}">{r["calls"]}</td><td style="{td}">{r["answered"]}</td>'
                             f'<td style="{td}">{r["talkMin"]}</td><td style="{td}">{r["appts"]}</td><td style="{td}">{r["score"] if r["score"] is not None else "—"}</td><td style="{td}"><b>{r["points"]}</b></td></tr>' for r in rs) + '</table>')
    try:   # الأهداف (الشهر لحد النهارده)
        import goals as G
        p = G.progress("month")
        prs = [r for r in p["reps"] if exts is None or r["ext"] in exts]
        if prs:
            col = lambda v: "#16a34a" if v >= 100 else "#0ea5e9" if v >= 70 else "#f59e0b" if v >= 40 else "#ef4444"  # noqa: E731
            out.append(f'<h3 style="margin:18px 0 6px">🏆 الأهداف — الشهر (عدّى {p["elapsed"]}% منه)</h3><table style="width:100%;border-collapse:collapse;font-size:14px">'
                       f'<tr style="background:#f1f5f9"><th style="{td}">الموظف</th>' + "".join(f'<th style="{td}">{e(m["name"])}</th>' for m in p["metrics"]) + f'<th style="{td}">الإجمالي</th></tr>'
                       + "".join(f'<tr><td style="{td}">{e(r["name"])}</td>' + "".join(
                           f'<td style="{td}">{"—" if r["m"][m["k"]]["pct"] is None else str(r["m"][m["k"]]["pct"]) + "%"}'
                           + (f'<br><small style="color:{"#dc2626" if (r["m"][m["k"]].get("pace") or 0) > r["m"][m["k"]].get("base", 0) else "#64748b"}">🎯 {r["m"][m["k"]]["pace"]}/يوم</small>'
                              if r["m"][m["k"]].get("pace") and r["m"][m["k"]].get("need", 0) > 0 else "")
                           + '</td>' for m in p["metrics"])
                           + f'<td style="{td}"><b style="color:{col(r["score"])}">{r["score"]}%</b></td></tr>' for r in prs) + '</table>'
                       + f'<div style="color:#64748b;font-size:12px;margin-top:4px">🎯 = المطلوب في اليوم في الـ {p["remDays"]} يوم شغل الباقيين عشان يحقق تارجت الشهر</div>'
                       + ("".join(f'<div style="margin-top:6px;padding:6px 10px;background:#fef3c7;border-radius:8px">🏖️ {e(h["name"])} {h["from"]}{" ← " + h["to"] if h["to"] != h["from"] else ""} — {h["lost"]} يوم شغل</div>' for h in p["holidays"][:3])))
    except Exception as ex:  # noqa: BLE001
        print("digest goals:", ex, flush=True)
    if cbs:
        out.append(f'<h3 style="margin:18px 0 6px;color:#b91c1c">📞 {len(cbs)} عميل اتصل ومحدش رد عليه لسه</h3><ul>'
                   + "".join(f'<li>{e(c["customer"] or c["number"])} — عند {e(c["name"])} — مستني من {c["waitMin"] // 60}س {c["waitMin"] % 60}د</li>' for c in cbs[:15]) + '</ul>')
    if fu:
        out.append(f'<h3 style="margin:18px 0 6px">🔔 {len(fu)} متابعة ميعادها جه ومتعملتش</h3><ul>'
                   + "".join(f'<li>{e(f["customer"] or f["number"])} — {e(f["name"])} — {f["due"]}</li>' for f in fu[:15]) + '</ul>')
    host = os.environ.get("NQ_HOST", "")
    if host:
        out.append(f'<p style="margin-top:20px"><a href="https://{e(host)}/calls/" style="background:#0f766e;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">افتح اللوحة</a></p>')
    out.append('<p style="color:#94a3b8;font-size:12px;margin-top:20px">NQ Call Monitor — بيتبعت أوتوماتيك كل يوم</p></div>')
    return "".join(out)


def email_digest() -> dict:
    """يبعت التقرير: الإيميلات العامة = كل الفرق، وكل مشرف ليه إيميل = فرقه بس"""
    s = settings()
    sent, errs = [], []
    subj = f"📊 تقرير مكالمات NQ — {now():%Y-%m-%d}"
    if s.get("emails"):
        r = mail_send(s["emails"], subj, digest_html(None, "كل الفرق"))
        (errs if r else sent).append(r or ", ".join(s["emails"]))
    if s.get("emailSupervisors", True):
        tm = CTX["teams"]() or {}
        tnames = {t["id"]: t["name"] for t in tm.get("teams", [])}
        assign = {str(k): v for k, v in (tm.get("assign") or {}).items()}
        for m in tm.get("managers", []):
            if not m.get("email") or m["email"] in (s.get("emails") or []):
                continue
            ids = set(m.get("teams") or [])
            ex = {e for e, t in assign.items() if t in ids}
            r = mail_send([m["email"]], subj, digest_html(ex, "، ".join(tnames.get(i, i) for i in ids)))
            (errs if r else sent).append(r or m["email"])
    return {"sent": sent, "errors": errs}


# ------------------------------------------------------------------ التنبيهات
def alerts() -> dict:
    return _jload("alerts.json", {"open": {}, "log": []})


def raise_alert(key: str, text: str, level="bad"):
    with LOCK:
        a = alerts()
        if key in a["open"]:
            if a["open"][key]["text"] != text:
                a["open"][key]["text"] = text
                _jsave("alerts.json", a)
            return
        a["open"][key] = {"key": key, "text": text, "level": level, "since": now().isoformat()}
        _jsave("alerts.json", a)
    tg_send(("🔴 " if level == "bad" else "🟠 ") + text)


def clear_alert(key: str, text: str = ""):
    with LOCK:
        a = alerts()
        x = a["open"].pop(key, None)
        if not x:
            return
        x["until"] = now().isoformat()
        a["log"] = ([x] + a["log"])[:100]
        _jsave("alerts.json", a)
    tg_send("✅ " + (text or ("اتحلّت: " + x["text"])))


# ------------------------------------------------------------------ فحص السيستم
def devices() -> list:
    """أجهزة الشرايح: جهاز n = dinastarN وعليه 4 تحويلات (جهاز 1: 100–103، جهاز 2: 104–107 …)"""
    base, per, n = int(os.environ.get("NQ_GW_BASE", "100")), int(os.environ.get("NQ_GW_PORTS", "4")), int(os.environ.get("NQ_GW_COUNT", "4"))
    return [{"trunk": f"{CTX['trunk']}{i}", "name": f"جهاز {i}", "no": i,
             "exts": [str(base + (i - 1) * per + k) for k in range(per)]} for i in range(1, n + 1)]


def device_of(ext) -> dict | None:
    return next((d for d in devices() if str(ext) in d["exts"]), None)


def gateways() -> dict:
    """{dinastarN: up/down} للأجهزة المتعرّفة على السنترال بس (الجهاز اللي لسه ماتضافش مش بيتحسب)"""
    out = asterisk("pjsip show contacts")
    eps = asterisk("pjsip show endpoints")
    if out is None:
        return {}
    known = set(re.findall(r"Endpoint:\s+(\S+?)(?:/\S*)?\s", eps or ""))
    res = {}
    for d in devices():
        t = d["trunk"]
        if known and t not in known:
            continue
        rows = [ln for ln in out.splitlines() if "Contact:" in ln and ln.split("Contact:")[1].strip().split("/")[0] == t]
        res[t] = "up" if any(re.search(r"\b(Avail|NonQual|Unknown)\b", r) for r in rows) else "down"
    return res


def gateway_status() -> str:
    """up / down / partial / unknown — مجموع كل الأجهزة"""
    g = STATE.get("gateways") or {}
    if not g:
        return "unknown"
    v = set(g.values())
    return "up" if v == {"up"} else "down" if v == {"down"} else "partial"


def gateway_for(ext) -> str:
    """حالة الجهاز اللي عليه تحويلة الموظف ده"""
    d = device_of(ext)
    g = STATE.get("gateways") or {}
    return g.get(d["trunk"], STATE["gateway"]) if d else STATE["gateway"]


def ext_online() -> dict:
    """التحويلات المتسجلة دلوقتي (تليفون مفتوح) → {ext: True}"""
    out = asterisk("pjsip show contacts") or ""
    on = {}
    for ln in out.splitlines():
        m = re.search(r"Contact:\s+(\d{2,6})/\S+\s+\S+\s+(Avail|NonQual|Unknown)", ln)
        if m:
            on[m.group(1)] = True
    return on


def presence() -> dict:
    """حالة كل تحويلة: talk (في مكالمة) / ring (بترن) / idle (متاحة) — اللي مش موجود = مش متصل"""
    out = asterisk("core show channels concise") or ""
    busy = {}
    for ln in out.splitlines():
        m = re.match(r"^PJSIP/(\d{2,6})-", ln)
        if m:
            f = ln.split("!")
            busy[m.group(1)] = "talk" if (len(f) > 4 and f[4] == "Up") or busy.get(m.group(1)) == "talk" else "ring"
    st = {e: "idle" for e in ext_online()}
    st.update(busy)
    return st


def failed_streak() -> tuple[int, int]:
    """مكالمات طالعة على الجيتواي آخر 20 دقيقة: (فشلت، اترد عليها)"""
    q = CTX["query"]
    if not q:
        return 0, 0
    since = (dt.datetime.now() - dt.timedelta(minutes=20)).replace(microsecond=0)
    try:
        rows = q("SELECT disposition, billsec FROM cdr WHERE calldate >= %s AND lastapp='Dial' AND lastdata LIKE %s",
                 (since, f"%{CTX['trunk']}%"))
    except Exception:  # noqa: BLE001
        return 0, 0
    bad = sum(1 for r in rows if r["disposition"] in ("FAILED", "CONGESTION"))
    ok = sum(1 for r in rows if r["disposition"] == "ANSWERED")
    return bad, ok


def backup_info() -> dict:
    b = _jload("backup.json", {})
    b["configured"] = bool(b.get("s3"))
    return b


def check_health():
    gw = gateways()
    if gw:
        STATE["gateways"] = gw
    g = gateway_status() if gw else "unknown"
    if g != STATE["gateway"]:
        STATE["gatewayAt"] = now().isoformat()
    STATE["gateway"], STATE["checked"] = g, now().isoformat()
    for d in devices():
        st = gw.get(d["trunk"])
        if st == "down":
            raise_alert("gateway:" + d["trunk"], f"{d['name']} (تحويلات {d['exts'][0]}–{d['exts'][-1]}) فاصل عن السنترال — "
                        "المكالمات بتاعته مش هتطلع. اتأكد إنه شغال وكابل النت فيه، وأصحاب التحويلات دي يكلموا من شرايحهم لحد ما يرجع.")
        elif st == "up":
            clear_alert("gateway:" + d["trunk"], f"{d['name']} رجع اتصل بالسنترال")
    clear_alert("gateway")   # التنبيه القديم (جهاز واحد)
    bad, ok = failed_streak()
    if bad >= 4 and ok == 0:
        raise_alert("failing", f"{bad} مكالمات طالعة فشلت آخر 20 دقيقة ومفيش ولا مكالمة نجحت — ممكن شريحة من غير رصيد أو شبكة")
    elif ok:
        clear_alert("failing", "المكالمات الطالعة رجعت تنجح")
    try:
        du = shutil.disk_usage(CTX["dir"])
        free_gb, pct = du.free / 2**30, du.free * 100 / du.total
        if free_gb < 3 or pct < 8:
            raise_alert("disk", f"المساحة على السيرفر قربت تخلص: فاضل {free_gb:.1f} جيجا ({pct:.0f}%)")
        elif free_gb > 4 and pct > 10:
            clear_alert("disk", "المساحة على السيرفر بقت كويسة")
    except OSError:
        pass
    b = backup_info()
    if b.get("at"):
        age = (now() - _ts(b["at"])).total_seconds() / 3600
        if not b.get("ok") or age > 36:
            raise_alert("backup", "النسخة الاحتياطية فشلت أو متعملتش من أكتر من يوم" +
                        (f": {b.get('error')}" if b.get("error") else ""), "warn")
        else:
            clear_alert("backup", "النسخة الاحتياطية رجعت تشتغل")


# ------------------------------------------------------------------ حد الشرايح اليومي
def lines_today() -> dict:
    """المكالمات الطالعة النهارده لكل تحويلة (من السنترال) + الحد"""
    s = settings()
    d = now().date()
    used = Counter(str(c["ext"]) for c in calls_c(d, d) if c["dir"] == "out" and c.get("source") != "sim")
    lines = CTX["lines"]()
    names = CTX["names"]()
    out = {}
    for ext in set(lines) | set(used):
        cap = int(s["cap"] or 0)
        n = used.get(ext, 0)
        out[ext] = {"ext": ext, "name": names.get(ext, ext), "line": lines.get(ext, ""), "used": n, "cap": cap,
                    "warn": bool(cap) and n >= cap * int(s["capWarn"]) / 100, "blocked": bool(cap) and n >= cap}
    return out


# ------------------------------------------------------------------ المكالمات الفائتة اللي محتاجة رد
# ------------------------------------------------------------------ قفل مهمة يدوي (من "المطلوب مني")
DONE_LOCK = threading.Lock()


def done_map() -> dict:
    return _jload("inbox_done.json", {"cb": {}, "fu": {}, "ld": {}})


def mark_done(kind: str, key: str, ext: str = "", undo: bool = False):
    """cb: الرقم → بيختفي لحد ما يتصل تاني | fu: الرقم|الميعاد | ld: الرقم"""
    if kind not in ("cb", "fu", "ld") or not key:
        raise HTTPException(400, "مهمة غلط")
    with DONE_LOCK:
        d = done_map()
        d.setdefault(kind, {})
        if undo:
            d[kind].pop(key, None)
        else:
            d[kind][key] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "ext": ext}
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).isoformat()
        for k in d:
            d[k] = {a: b for a, b in d[k].items() if b.get("at", "") >= cut}
        (CTX["dir"] / "inbox_done.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def callbacks(days: int | None = None) -> list:
    days = days or int(settings()["callbackDays"])
    d2 = now().date()
    calls = sorted(calls_c(d2 - dt.timedelta(days=days), d2), key=lambda c: c["time"])
    by_num: dict = {}
    for c in calls:
        d = digits(c.get("number"))
        if len(d) < 7:
            continue
        by_num.setdefault(d, []).append(c)
    names = CTX["names"]()
    done = done_map().get("cb", {})
    out = []
    for d, cs in by_num.items():
        last_ok = max((c["time"] for c in cs if c["status"] == "تم الرد"), default="")
        missed = [c for c in cs if c["dir"] == "in" and c["status"] != "تم الرد" and c["time"] > last_ok]
        if not missed:
            continue
        if d in done:   # اتقفلت يدوي — بترجع بس لو اتصل تاني بعد القفل
            try:
                if _ts(missed[-1]["time"]) <= _ts(done[d]["at"]):
                    continue
            except (KeyError, ValueError):
                continue
        first, last = missed[0], missed[-1]
        tries = [c for c in cs if c["dir"] == "out" and c["time"] > first["time"]]
        own = CTX["owner_of"](last.get("number")) or {}
        ext = str(own.get("ext") or last["ext"])
        note = next((c.get("note") for c in reversed(cs) if c.get("note")), None) or {}
        out.append({"number": last.get("number"), "digits": d, "missed": len(missed), "first": first["time"],
                    "last": last["time"], "ext": ext, "name": names.get(ext, ext), "owner": bool(own),
                    "tries": len(tries), "lastTry": tries[-1]["time"] if tries else None,
                    "customer": note.get("customer") or "",
                    "waitMin": int((dt.datetime.now(dt.timezone.utc) - _ts(first["time"])).total_seconds() // 60)})
    out.sort(key=lambda x: x["first"])
    return out


# ------------------------------------------------------------------ المتابعات (تاريخ المتابعة في ملخص المكالمة)
def followups(ext: str | None = None) -> list:
    today = now().date()
    notes = CTX["notes"](today - dt.timedelta(days=45), today + dt.timedelta(days=1))
    calls = calls_c(today - dt.timedelta(days=45), today)
    later: dict = {}
    for c in calls:
        later.setdefault(digits(c.get("number")), []).append(c["time"])
    names = CTX["names"]()
    done_fu = done_map().get("fu", {})
    seen, out = set(), []
    for n in sorted(notes, key=lambda x: x.get("started") or "", reverse=True):
        fu = str(n.get("followUp") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fu) or (ext and str(n.get("ext")) != str(ext)):
            continue
        d = digits(n.get("number"))
        if not d or d in seen:
            continue
        seen.add(d)
        due = dt.date.fromisoformat(fu)
        if due > today or f"{d}|{fu}" in done_fu:
            continue
        try:   # اتكلّم بعد الملاحظة بـ 10 دقايق؟ يبقى المتابعة اتعملت
            st = _ts(n["started"]) + dt.timedelta(minutes=10)
            if any(_ts(t) > st for t in later.get(d, [])):
                continue
        except (KeyError, ValueError):
            pass
        out.append({"number": n.get("number"), "customer": n.get("customer") or "", "due": fu, "key": f"{d}|{fu}",
                    "late": (today - due).days, "summary": (n.get("summary") or "")[:240],
                    "ext": str(n.get("ext")), "name": names.get(str(n.get("ext")), "")})
    out.sort(key=lambda x: x["due"])
    return out


# ------------------------------------------------------------------ العملاء الجدد من الـ CRM
def poll_leads():
    if not settings()["leads"] or not CTX["crm_on"]() or not CTX["crm_get"]:
        return
    try:
        items = CTX["crm_get"]("/api/v1/customers-potential", {"PageNumber": 1, "PageSize": 50})
    except Exception as e:  # noqa: BLE001
        print("leads poll failed:", e, flush=True)
        return
    st = _jload("leads.json", {"seen": [], "items": []})
    first_run = not st["seen"]
    seen = set(st["seen"])
    by_sales = {str(v): k for k, v in CTX["sales_ids"]().items()}
    names = CTX["names"]()
    for c in items:
        lid = str(c.get("customersPotentialId") or c.get("id") or "")
        if not lid or lid in seen:
            continue
        seen.add(lid)
        if first_run:
            continue
        sp = c.get("salesPerson")
        sid = str(c.get("salesPersonId") or c.get("salesManId") or c.get("employeeId")
                  or (sp.get("id") if isinstance(sp, dict) else "") or "")
        ext = by_sales.get(sid, "")
        st["items"].insert(0, {"id": lid, "name": c.get("customerName") or c.get("name") or "",
                               "phone": c.get("phoneNumber") or c.get("mobile") or c.get("phone") or "",
                               "ext": ext, "repName": names.get(ext, ""), "at": now().isoformat()})
        if not ext:
            tg_send(f"🆕 عميل جديد في الـ CRM مش متوزّع: {st['items'][0]['name']} {st['items'][0]['phone']}")
    st["seen"] = list(seen)[-2000:]
    st["items"] = st["items"][:300]
    _jsave("leads.json", st)


def leads(ext: str | None = None, days=3) -> list:
    st = _jload("leads.json", {"items": []})
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    today = now().date()
    called = {digits(c.get("number")) for c in calls_c(today - dt.timedelta(days=days), today)}
    done_ld = done_map().get("ld", {})
    out = []
    for x in st["items"]:
        if _ts(x["at"]) < cut or (ext and x["ext"] != str(ext)) or digits(x["phone"]) in done_ld:
            continue
        out.append({**x, "called": digits(x["phone"]) in called,
                    "ageMin": int((dt.datetime.now(dt.timezone.utc) - _ts(x["at"])).total_seconds() // 60)})
    return out


# ------------------------------------------------------------------ مباشر: مين في مكالمة دلوقتي
def live() -> list:
    out = asterisk("core show channels concise") or ""
    chans: dict = {}
    for ln in out.splitlines():
        f = ln.split("!")
        if len(f) < 12:
            continue
        m = re.match(r"^PJSIP/(\d{2,6})-", f[0])
        if not m:
            continue
        ext, state, app, data, cid = m.group(1), f[4], f[5], f[6], f[7]
        try:
            dur = int(f[11] or 0)
        except ValueError:
            dur = 0
        num, direction = "", "in"
        if app == "Dial":
            direction = "out"
            mm = re.search(r"/(\+?\d{6,})@", data) or re.search(r"(\+?\d{6,})", f[2])
            num = mm.group(1) if mm else f[2]
            if num.startswith(ext) and len(num) > 10:
                num = num[len(ext):]
        elif app == "ChanSpy":
            direction, num = "spy", data.split(",")[0].replace("PJSIP/", "").rstrip("-")
        else:
            num = cid if cid and cid != ext else f[2]
        st = "talk" if state == "Up" else "ring"
        prev = chans.get(ext)
        if not prev or (st == "talk" and prev["state"] != "talk"):
            chans[ext] = {"state": st, "sec": dur, "number": num, "dir": direction}
    online = ext_online()
    names = CTX["names"]()
    sc = CTX["scope"]()
    res = []
    for r in CTX["reps"]():
        e = str(r["id"])
        if sc is not None and e not in sc["exts"]:
            continue
        c = chans.get(e)
        res.append({"ext": e, "name": names.get(e, r.get("name") or e), "team": r.get("team", ""),
                    "state": c["state"] if c else ("idle" if online.get(e) else "off"),
                    "sec": c["sec"] if c else 0, "number": c["number"] if c else "", "dir": c["dir"] if c else ""})
    order = {"talk": 0, "ring": 1, "idle": 2, "off": 3}
    res.sort(key=lambda x: (order[x["state"]], -x["sec"]))
    return res


def sync_spy():
    """صلاحيات اسمع/وشوش/ادخل في السنترال: قائد الفريق على فريقه بس، والمدير (تحويلاته في الإعدادات) على الكل"""
    tm = CTX["teams"]() or {}
    assign = {str(k): v for k, v in (tm.get("assign") or {}).items()}
    allow = {}
    for t in tm.get("teams", []):
        le = str(t.get("leaderExt") or "").strip()
        if le:
            allow[le] = ",".join(sorted(e for e, tid in assign.items() if tid == t["id"] and e != le))
    for e in settings()["spyExts"]:
        allow[str(e)] = "*"
    sig = json.dumps(allow, sort_keys=True)
    if sig == STATE["spySig"]:
        return
    if asterisk("database deltree NQSPY") is None:
        return
    for k, v in allow.items():
        if v:
            asterisk(f"database put NQSPY {k} {v}")
    STATE["spySig"] = sig


# ------------------------------------------------------------------ ترتيب الموظفين + الملخص اليومي
def board(d1: dt.date, d2: dt.date) -> list:
    names = CTX["names"]()
    tm = CTX["teams"]() or {}
    tnames = {t["id"]: t["name"] for t in tm.get("teams", [])}
    assign = {str(k): v for k, v in (tm.get("assign") or {}).items()}
    sc = CTX["scope"]()
    rows: dict = {}

    def row(e):
        e = str(e)
        return rows.setdefault(e, {"ext": e, "name": names.get(e, e), "team": tnames.get(assign.get(e), ""),
                                   "calls": 0, "answered": 0, "talkMin": 0, "appts": 0, "_sc": []})
    for c in calls_c(d1, d2):
        r = row(c["ext"])
        r["calls"] += 1
        if c["status"] == "تم الرد":
            r["answered"] += 1
            r["talkMin"] += int(c.get("talkSec") or 0) / 60
    lo = dt.datetime.combine(d1, dt.time.min, CTX["tz"])
    hi = dt.datetime.combine(d2 + dt.timedelta(days=1), dt.time.min, CTX["tz"])
    for a in CTX["appointments"]():
        try:
            if a.get("status") != "rejected" and lo <= _ts(a.get("created")) < hi:
                row(a.get("ext"))["appts"] += 1
        except (TypeError, ValueError):
            pass
    if CTX["analysis"]:
        for x in CTX["analysis"](d1, d2):
            s = (x.get("analysis") or {}).get("agent_score")
            if isinstance(s, (int, float)):
                row(x["call"]["ext"])["_sc"].append(s)
    out = []
    rep_ok = CTX.get("report_exts", lambda: None)()   # الفرق اللي فئتها مش بتتحسب (تدريب، ما بعد البيع…) بره الترتيب
    for r in rows.values():
        if sc is not None and r["ext"] not in sc["exts"]:
            continue
        if rep_ok is not None and r["ext"] not in rep_ok:
            continue
        sc_ = r.pop("_sc")
        r["score"] = round(sum(sc_) / len(sc_)) if sc_ else None
        r["talkMin"] = round(r["talkMin"])
        # النقط: الميعاد بـ 20، المكالمة اللي اترد عليها بـ 2، كل دقيقة كلام بـ 1، وجودة المكالمة بتزود لحد 30%
        pts = r["appts"] * 20 + r["answered"] * 2 + r["talkMin"]
        if r["score"]:
            pts *= 0.7 + 0.6 * r["score"] / 100
        r["points"] = round(pts)
        out.append(r)
    out.sort(key=lambda r: -r["points"])
    return out


def digest_text() -> str:
    d = now().date()
    rows = board(d, d)
    cbs = callbacks(1)
    tm = CTX["teams"]() or {}
    lines = [f"📊 ملخص النهارده {d:%Y-%m-%d}"]
    tot = lambda k, rs: sum(r[k] for r in rs)  # noqa: E731
    lines.append(f"المكالمات {tot('calls', rows)} · اترد {tot('answered', rows)} · كلام {tot('talkMin', rows)} دقيقة · مواعيد {tot('appts', rows)}")
    for t in tm.get("teams", []) + [{"name": "بدون فريق", "id": None}]:
        rs = [r for r in rows if (r["team"] or "بدون فريق") == t["name"]]
        if not rs:
            continue
        lines.append(f"\n👥 {t['name']}: {tot('calls', rs)} مكالمة · {tot('appts', rs)} ميعاد")
        for r in rs[:8]:
            lines.append(f"  • {r['name']}: {r['calls']} مكالمة، {r['answered']} اترد، {r['talkMin']}د، {r['appts']} ميعاد"
                         + (f"، تقييم {r['score']}" if r["score"] is not None else ""))
    if rows:
        lines.append(f"\n🏆 الأول النهارده: {rows[0]['name']} ({rows[0]['points']} نقطة)")
    if cbs:
        lines.append(f"\n📞 {len(cbs)} عميل اتصل ومحدش رد عليه لسه:")
        for c in cbs[:8]:
            lines.append(f"  • {c['customer'] or c['number']} — عند {c['name']} — مستني من {c['waitMin'] // 60}س {c['waitMin'] % 60}د")
    fu = followups()
    if fu:
        lines.append(f"\n🔔 {len(fu)} متابعة ميعادها جه ومتعملتش")
    return "\n".join(lines)


def training_progress() -> list:
    """خطة التدريب الأخيرة: تقييم كل موظف وقت عمل الخطة ← تقييمه من ساعتها"""
    plans = CTX["training"]() or {}
    if not plans or not CTX["analysis"]:
        return []
    p = plans[max(plans, key=lambda k: plans[k].get("generated", ""))]
    since = _ts(p["generated"])
    d1, d2 = since.astimezone(CTX["tz"]).date(), now().date()
    cur: dict = {}
    for x in CTX["analysis"](d1, d2):
        s = (x.get("analysis") or {}).get("agent_score")
        try:
            if isinstance(s, (int, float)) and _ts(x["call"]["time"]) >= since:
                cur.setdefault(str(x["call"]["ext"]), []).append(s)
        except (KeyError, ValueError):
            pass
    sc = CTX["scope"]()
    out = []
    for r in p.get("reps") or []:
        e = str(r.get("ext"))
        if sc is not None and e not in sc["exts"]:
            continue
        v = cur.get(e, [])
        now_avg = round(sum(v) / len(v), 1) if v else None
        out.append({"ext": e, "name": r.get("name", e), "team": r.get("team", ""), "before": r.get("avg"),
                    "now": now_avg, "calls": len(v),
                    "delta": round(now_avg - r["avg"], 1) if now_avg is not None and r.get("avg") is not None else None})
    out.sort(key=lambda x: (x["delta"] is None, -(x["delta"] or 0)))
    return out


# ------------------------------------------------------------------ اللوب
def tick():
    check_health()
    sync_spy()
    t = now()
    if time.time() - STATE["leadsAt"] > 600:
        STATE["leadsAt"] = time.time()
        poll_leads()
    s = settings()
    day = t.strftime("%Y-%m-%d")
    try:
        import workcal
        if STATE.get("calDay") != day:   # مرة في اليوم: نحمّل الأجازات الرسمية للسنة الجاية لو ناقصة
            STATE["calDay"] = day
            c = workcal.load()
            for ctry in c["countries"]:
                # السنة الناقصة بس (السنة دي، وفي ديسمبر اللي بعدها) — من غير مانمسح تعديلات الأدمن
                miss = [y for y in (t.year, t.year + 1) if (y == t.year or t.month == 12) and not any(
                    h.get("source") == "auto" and h.get("country") == ctry and h["from"][:4] == str(y) for h in c["holidays"])]
                if miss and (CTX["dir"] / "workcal.json").exists():
                    try:
                        workcal.CTX.update(dir=CTX["dir"], tz=CTX["tz"])
                        new = workcal.auto_holidays(ctry, miss)
                        with workcal.LOCK:
                            c = workcal.load()
                            keep = [h for h in c["holidays"] if not (h.get("source") == "auto" and h.get("country") == ctry and int(h["from"][:4]) in miss)]
                            c["holidays"] = sorted(keep + new, key=lambda x: x["from"])
                            workcal._save(c)
                    except Exception as e:  # noqa: BLE001
                        print("holidays autoload:", e, flush=True)
        if (CTX["dir"] / "workcal.json").exists():
            due, why = workcal.report_due(t)
        else:
            due, why = t.hour >= int(s["digestHour"]), ""
    except Exception as e:  # noqa: BLE001
        print("workcal:", e, flush=True)
        due, why = t.hour >= int(s["digestHour"]), ""
    if not due and why and STATE.get("skipNote") != day:
        STATE["skipNote"] = day
        print("digest skipped:", why, flush=True)
    if due and STATE["digestDay"] != day and _jload("digest.json", {}).get("day") != day:
        txt = digest_text()
        _jsave("digest.json", {"day": day, "text": txt, "at": t.isoformat()})
        STATE["digestDay"] = day
        tg_send(txt)
        if mail_ready():
            threading.Thread(target=email_digest, daemon=True).start()
        if t.weekday() == 3:   # الخميس: تقدّم خطة التدريب
            pr = [x for x in training_progress() if x["delta"] is not None]
            if pr:
                tg_send("📈 متابعة خطة التدريب (التقييم قبل ← دلوقتي):\n" + "\n".join(
                    f"  • {x['name']}: {x['before']} ← {x['now']} ({'+' if x['delta'] >= 0 else ''}{x['delta']})" for x in pr))


def monitor_loop():
    time.sleep(20)
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            print("ops tick failed:", repr(e), flush=True)
        time.sleep(60)


def start():
    if os.environ.get("NQ_NO_MONITOR"):
        return
    threading.Thread(target=monitor_loop, daemon=True, name="nq-ops").start()


# ------------------------------------------------------------------ API اللوحة
def _range(period: str):
    d2 = now().date()
    return {"today": d2, "week": d2 - dt.timedelta(days=(d2.weekday() + 1) % 7),   # الأسبوع بيبدأ الأحد
            "month": d2.replace(day=1)}.get(period, d2), d2


@router.get("/api/health")
def health():
    a = alerts()
    g = STATE.get("gateways") or {}
    return {"gateway": STATE["gateway"], "gatewayAt": STATE["gatewayAt"], "checked": STATE["checked"],
            "devices": [{**d, "status": g.get(d["trunk"], "none")} for d in devices()],
            "alerts": list(a["open"].values()), "log": a["log"][:15], "backup": backup_info(),
            "telegram": tg_ready(), "lines": sorted(lines_today().values(), key=lambda x: -x["used"]),
            "disk": _disk()}


def _disk():
    try:
        du = shutil.disk_usage(CTX["dir"])
        return {"freeGb": round(du.free / 2**30, 1), "pct": round(du.free * 100 / du.total)}
    except OSError:
        return None


@router.get("/api/live")
def api_live():
    return {"reps": live(), "canSpy": True}


@router.get("/api/callbacks")
def api_callbacks(days: int = 0):
    sc = CTX["scope"]()
    items = [c for c in callbacks(days or None) if sc is None or c["ext"] in sc["exts"]]
    fu = [f for f in followups() if sc is None or f["ext"] in sc["exts"]]
    ld = [x for x in leads() if sc is None or x["ext"] in sc["exts"]]
    return {"callbacks": items, "followups": fu, "leads": ld}


@router.get("/api/board")
def api_board(period: str = "today"):
    d1, d2 = _range(period)
    return {"period": period, "from": str(d1), "to": str(d2), "rows": board(d1, d2), "live": live()}


@router.get("/api/digest")
def api_digest():
    return {"text": digest_text(), "last": _jload("digest.json", {})}


@router.post("/api/ops/digest-now")
def digest_now():
    if not tg_ready():
        raise HTTPException(400, "تليجرام مش متظبط")
    if not tg_send(digest_text()):
        raise HTTPException(502, "تليجرام رفض الرسالة")
    return {"ok": True}


@router.get("/api/training/progress")
def api_progress():
    return {"items": training_progress()}


@router.get("/api/ops/settings")
def get_ops():
    return {**settings(), "telegram": tg_ready(), "email": mail_ready(), "emailFrom": smtp_conf().get("user", "")}


@router.put("/api/ops/settings")
async def put_ops(request: Request):
    b = await request.json()
    s = settings()
    for k in ("cap", "capWarn", "digestHour", "callbackDays"):
        if k in b:
            try:
                s[k] = max(0, int(b[k]))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} لازم يبقى رقم")
    s["capWarn"] = min(100, max(10, s["capWarn"]))
    s["digestHour"] = min(23, s["digestHour"])
    s["callbackDays"] = min(14, max(1, s["callbackDays"]))
    if "spyExts" in b:
        s["spyExts"] = [e for e in (str(x).strip() for x in b["spyExts"] or []) if re.fullmatch(r"\d{2,6}", e)]
    if "leads" in b:
        s["leads"] = bool(b["leads"])
    if "emails" in b:
        em = [x.strip() for x in (b["emails"] if isinstance(b["emails"], list) else re.split(r"[,\s;]+", str(b["emails"]))) if str(x).strip()]
        bad = [x for x in em if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x)]
        if bad:
            raise HTTPException(400, f"إيميل غلط: {bad[0]}")
        s["emails"] = em[:20]
    if "emailSupervisors" in b:
        s["emailSupervisors"] = bool(b["emailSupervisors"])
    _jsave("ops.json", {k: s[k] for k in DEFAULTS})
    STATE["spySig"] = ""
    return get_ops()


@router.get("/api/ops/smtp")
def get_smtp():
    c = smtp_conf()
    return {"host": c["host"], "port": c["port"], "user": c["user"], "name": c.get("name", ""), "from": c.get("from", ""),
            "hasPassword": bool(c["password"]), "ready": mail_ready()}


@router.put("/api/ops/smtp")
async def put_smtp(request: Request):
    """الأدمن يظبط الإيميل اللي هيبعت منه — بنجرّب الدخول قبل ما نحفظ. الباسورد عمره ما بيرجع للشاشة"""
    b = await request.json()
    old = smtp_conf()
    c = {"host": str(b.get("host") or "").strip()[:120], "user": str(b.get("user") or "").strip()[:160],
         "name": str(b.get("name") or "NQ Call Center").strip()[:80], "from": str(b.get("from") or "").strip()[:160],
         "password": str(b.get("password") or "").replace(" ", "") or (old["password"] if str(b.get("user") or "").strip() == old["user"] else "")}
    try:
        c["port"] = int(b.get("port") or 587)
    except (TypeError, ValueError):
        raise HTTPException(400, "البورت لازم يبقى رقم")
    if not re.fullmatch(r"[\w.\-]+", c["host"]) or not c["user"]:
        raise HTTPException(400, "اكتب السيرفر والإيميل")
    if not c["password"]:
        raise HTTPException(400, "اكتب الباسورد")
    try:
        smtp_login(c).quit()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"السيرفر رفض الدخول — اتأكد من البيانات (Gmail لازم App Password): {str(e)[:160]}")
    f = CTX["dir"] / "smtp.json"
    f.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return get_smtp()


@router.post("/api/ops/email-test")
def ops_email_test():
    """يبعت تقرير النهارده دلوقتي لكل الإيميلات (تجربة)"""
    if not mail_ready():
        raise HTTPException(400, "الإيميل مش متظبط — من الإعدادات ← 📧 إعدادات الإيميل")
    r = email_digest()
    if not r["sent"] and not r["errors"]:
        raise HTTPException(400, "مفيش إيميلات — اكتب إيميلات المديرين الأول")
    if r["errors"] and not r["sent"]:
        raise HTTPException(502, "الإيميل مااتبعتش: " + r["errors"][0])
    return r


@router.post("/api/ops/test")
def ops_test():
    if not tg_ready():
        raise HTTPException(400, "تليجرام مش متظبط — على السيرفر: sudo nq-setkey telegram")
    if not tg_send("👋 تجربة من لوحة NQ — التنبيهات هتوصل هنا"):
        raise HTTPException(502, "تليجرام رفض الرسالة — اتأكد من التوكن ورقم الشات")
    return {"ok": True}


# ------------------------------------------------------------------ API التطبيق: المطلوب مني
@router.post("/app/inbox/done")
async def app_inbox_done(request: Request):
    """المندوب يقفل مهمة يدوي (أو يرجّعها)"""
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    mark_done(str(b.get("kind") or ""), str(b.get("key") or ""), ext, bool(b.get("undo")))
    return JSONResponse({"ok": True}, headers=HDR)


@router.post("/api/inbox/done")
async def api_inbox_done(request: Request):
    b = await request.json()
    sc = CTX["scope"]()
    mark_done(str(b.get("kind") or ""), str(b.get("key") or ""), (sc or {}).get("user", "admin"), bool(b.get("undo")))
    return {"ok": True}


@router.post("/app/inbox")
async def app_inbox(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not CTX["check_ext"](ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    ln = lines_today().get(ext) or {"used": 0, "cap": int(settings()["cap"] or 0), "warn": False, "blocked": False}
    try:
        import wainbox
        wa_unread = wainbox.unread_for(ext) if wainbox.ready() else 0
    except Exception:  # noqa: BLE001
        wa_unread = 0
    d = device_of(ext)
    me = {"ext": ext, "name": CTX["names"]().get(ext, ""), "sim": CTX["lines"]().get(ext, ""),
          "device": d["no"] if d else None, "port": d["exts"].index(ext) if d else None,
          "deviceUp": gateway_for(ext) == "up", **(CTX.get("sims", lambda: {})().get(ext) or {})}
    return JSONResponse({"gateway": gateway_for(ext), "me": me, "line": ln, "waUnread": wa_unread,
                         "callbacks": [c for c in callbacks() if c["ext"] == ext],
                         "followups": followups(ext), "leads": [x for x in leads(ext) if not x["called"]]},
                        headers=HDR)


# ------------------------------------------------------------------ أحداث أجهزة الشرايح (Dinstar Event report)
# كل جهاز بيبعت أحداثه (تسجيل الشريحة / شيلها) على: https://<السيرفر>/calls-app/gw-event?d=<رقم الجهاز>&k=<المفتاح>
# لما شريحة تتشال أو تقف → تنبيه (تليجرام + اللوحة)، ولما ترجع → التنبيه يتقفل.
SIM_OFF = re.compile(r"no[\s_-]*u?sim|sim[\s_-]*(absent|remov|not[\s_-]*insert|out)|absent|removed|not[\s_-]*insert|unregister|power[\s_-]*off|no[\s_-]*card|not[\s_-]*registered|deregist", re.I)
SIM_ON = re.compile(r"\bregistered\b|mobile[\s_-]*registered|sim[\s_-]*(ok|ready|insert)|\bready\b|\bup\b", re.I)


def gw_key() -> str:
    f = CTX["dir"] / "gw_key.txt"
    try:
        return f.read_text().strip()
    except OSError:
        import secrets as _s
        k = _s.token_urlsafe(18)
        f.write_text(k)
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        return k


def _flat(x, pre="") -> dict:
    out = {}
    if isinstance(x, dict):
        for k, v in x.items():
            out.update(_flat(v, f"{pre}{k}."))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            out.update(_flat(v, f"{pre}{i}."))
    else:
        out[pre[:-1]] = x
    return out


def _port_events(data) -> list:
    """بيطلّع [(port, state)] من أي شكل JSON/فورم — state = off / on / None"""
    rows = []
    items = data if isinstance(data, list) else (data.get("ports") or data.get("port_status") or data.get("data") or [data]) if isinstance(data, dict) else []
    if isinstance(items, dict):
        items = [items]
    for it in items if isinstance(items, list) else []:
        f = _flat(it) if isinstance(it, (dict, list)) else {"value": it}
        port = None
        for k, v in f.items():
            if re.search(r"(^|\.)(port|port_?no|portid|slot|channel)$", k, re.I) and str(v).strip().lstrip("-").isdigit():
                port = int(str(v).strip())
                break
        txt = " ".join(f"{k}={v}" for k, v in f.items() if not re.search(r"port|time|date|sn|mac|ip|id$", k, re.I))
        st = "off" if SIM_OFF.search(txt) else "on" if SIM_ON.search(txt) else None
        if port is not None:
            rows.append((port, st, txt[:200]))
    return rows


@router.api_route("/app/gw-event", methods=["GET", "POST"])
async def gw_event(request: Request):
    q = request.query_params
    if q.get("k") != gw_key():
        return JSONResponse({"detail": "forbidden"}, 403)
    raw = (await request.body())[:20000].decode("utf-8", "replace")
    try:
        data = json.loads(raw) if raw.strip()[:1] in "[{" else dict(urllib.parse.parse_qsl(raw)) or dict(q)
    except ValueError:
        data = {"raw": raw}
    dev = int(q.get("d") or 0) if str(q.get("d") or "").isdigit() else 0
    with LOCK:
        log = _jload("gw_events.json", [])
        log = ([{"at": now().isoformat(), "d": dev, "data": data if len(raw) < 4000 else raw[:4000]}] + log)[:80]
        _jsave("gw_events.json", log)
    devs = devices()
    d = devs[dev - 1] if 1 <= dev <= len(devs) else None
    names = CTX["names"]() if CTX.get("names") else {}
    for port, st, txt in _port_events(data):
        if not d or not (0 <= port < len(d["exts"])):
            continue
        ext = d["exts"][port]
        key = f"sim:{ext}"
        who = names.get(ext, "")
        if st == "off":
            raise_alert(key, f"📵 شريحة تحويلة {ext}{(' (' + who + ')') if who else ''} اتشالت أو وقفت — {d['name']} بورت {port}")
        elif st == "on":
            clear_alert(key, f"📶 شريحة تحويلة {ext}{(' (' + who + ')') if who else ''} رجعت تشتغل — {d['name']} بورت {port}")
    return {"ok": True}


@router.get("/api/ops/gw-events")
def api_gw_events(request: Request):
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host", "")).split(",")[0].strip()
    base = f"https://{host}/calls-app/gw-event"
    return {"urls": [{"device": x["name"], "url": f"{base}?d={x['no']}&k={gw_key()}"} for x in devices()],
            "events": _jload("gw_events.json", [])[:30]}
