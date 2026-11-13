"""
NQ Calls — خدمة لوحة المكالمات على سيرفر السنترال
  - تقرا سجل المكالمات (CDR) من قاعدة بيانات FreePBX
  - تشغّل التسجيلات
  - تشغّل تحليل الذكاء الاصطناعي لما يتطلب (أو أسبوعيًا من cron)
بتشتغل على 127.0.0.1:8090 وApache بيوصّلها على https://<host>/calls/ ومعاها باسورد.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
CONF_DIR = Path(os.environ.get("NQ_CONF", "/etc/nq-calls"))
DATA_DIR = Path(os.environ.get("NQ_DATA", "/var/lib/nq-calls"))
ANALYSIS_DIR = DATA_DIR / "analysis"
MONITOR = Path(os.environ.get("NQ_MONITOR", "/var/spool/asterisk/monitor"))
TZ = ZoneInfo(os.environ.get("NQ_TZ", "Asia/Riyadh"))
TRUNK = "PJSIP/" + os.environ.get("NQ_TRUNK", "dinastar1")

app = FastAPI(title="NQ Calls", docs_url=None, redoc_url=None)


# ---------------- DB ----------------
def db_conf() -> dict:
    """بيانات الدخول من /etc/freepbx.conf (نفس اللي FreePBX بيستخدمها)"""
    if os.environ.get("NQ_DB_USER"):
        return dict(host=os.environ.get("NQ_DB_HOST", "localhost"), user=os.environ["NQ_DB_USER"],
                    password=os.environ.get("NQ_DB_PASS", ""))
    txt = Path("/etc/freepbx.conf").read_text()
    get = lambda k: re.search(rf"\['{k}'\]\s*=\s*['\"]([^'\"]*)['\"]", txt).group(1)
    return dict(host=get("AMPDBHOST"), user=get("AMPDBUSER"), password=get("AMPDBPASS"))


def query(sql: str, args: tuple) -> list:
    c = db_conf()
    con = pymysql.connect(host=c["host"], user=c["user"], password=c["password"],
                          database=os.environ.get("NQ_CDR_DB", "asteriskcdrdb"),
                          cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    try:
        with con.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        con.close()


def ext_from(chan: str) -> int:
    m = re.match(r"^PJSIP/(\d{2,5})-", chan or "")
    return int(m.group(1)) if m else 0


def strip_prefix(dst: str, ext: int) -> str:
    p = str(ext)
    return dst[len(p):] if len(dst) > 10 and dst.startswith(p) else dst


def stem(recordingfile: str) -> str:
    return Path(recordingfile).stem if recordingfile else ""


# ---------------- config ----------------
@app.get("/api/config")
def config():
    f = CONF_DIR / "reps.json"
    reps = json.loads(f.read_text()) if f.exists() else {"teams": [], "reps": []}
    reps["aiEnabled"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return reps


# ---------------- calls ----------------
@app.get("/api/calls")
def calls(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    start = dt.datetime.combine(date_from, dt.time.min, TZ).astimezone(dt.timezone.utc).replace(tzinfo=None)
    end = dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min, TZ).astimezone(dt.timezone.utc).replace(tzinfo=None)
    rows = query(
        """SELECT uniqueid, linkedid, calldate, src, cnum, dst, channel, dstchannel,
                  disposition, duration, billsec, recordingfile
           FROM cdr
           WHERE calldate >= %s AND calldate < %s
             AND (channel LIKE %s OR dstchannel LIKE %s)
           ORDER BY calldate""",
        (start, end, TRUNK + "%", TRUNK + "%"))

    groups: dict = {}
    for r in rows:
        groups.setdefault(r["linkedid"] or r["uniqueid"], []).append(r)

    out = []
    for g in groups.values():
        main = next((r for r in g if r["disposition"] == "ANSWERED"), g[-1])
        inbound = (main["channel"] or "").startswith(TRUNK)
        ext = ext_from(main["dstchannel"]) if inbound else ext_from(main["channel"])
        if not ext:
            continue
        status = {"ANSWERED": "تم الرد", "BUSY": "مرفوضة"}.get(main["disposition"], "فائتة" if inbound else "لم يرد")
        rec = next((r["recordingfile"] for r in g if r["recordingfile"]), "")
        t = main["calldate"].replace(tzinfo=dt.timezone.utc)
        out.append(dict(
            id=main["uniqueid"], time=t.isoformat(), ext=ext, dir="in" if inbound else "out",
            status=status, talkSec=int(main["billsec"] or 0),
            ringSec=max(0, int(main["duration"] or 0) - int(main["billsec"] or 0)),
            number=main["src"] if inbound else strip_prefix(main["dst"] or "", ext),
            customer=None,
            hasRecording=bool(rec) and main["disposition"] == "ANSWERED",
            analyzed=bool(rec) and (ANALYSIS_DIR / (stem(rec) + ".json")).exists(),
        ))
    out.sort(key=lambda c: c["time"], reverse=True)
    return out


def _recording_row(call_id: str) -> dict:
    rows = query("SELECT calldate, recordingfile FROM cdr WHERE (uniqueid=%s OR linkedid=%s) AND recordingfile<>'' LIMIT 1",
                 (call_id, call_id))
    if not rows:
        raise HTTPException(404, "مفيش تسجيل للمكالمة دي")
    return rows[0]


def _recording_path(row: dict) -> Path:
    name = Path(row["recordingfile"]).name  # يمنع ../
    d = row["calldate"]
    p = MONITOR / f"{d:%Y}" / f"{d:%m}" / f"{d:%d}" / name
    if not p.exists():
        hits = list(MONITOR.glob(f"**/{name}"))
        if not hits:
            raise HTTPException(404, "ملف التسجيل مش موجود على السيرفر")
        p = hits[0]
    return p


@app.get("/api/recording/{call_id}")
def recording(call_id: str, download: int = 0):
    p = _recording_path(_recording_row(call_id))
    return FileResponse(p, media_type="audio/wav", filename=p.name if download else None)


# ---------------- AI analysis ----------------
JOB = {"running": False, "started": None, "finished": None, "log": "", "range": None}
JOB_LOCK = threading.Lock()


def _run_job(d_from: str, d_to: str):
    cmd = ["nice", "-n", "19", sys.executable, str(HERE / "analyzer.py"), "--from", d_from, "--to", d_to]
    env = dict(os.environ, NQ_AI_OUT=str(ANALYSIS_DIR))
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)
        JOB["log"] = (p.stdout + p.stderr)[-4000:]
    except Exception as e:  # noqa: BLE001
        JOB["log"] = f"فشل التحليل: {e}"
    finally:
        JOB["running"] = False
        JOB["finished"] = dt.datetime.now(TZ).isoformat()


@app.post("/api/analyze")
def analyze(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "مفتاح Claude API مش متضاف على السيرفر")
    with JOB_LOCK:
        if JOB["running"]:
            raise HTTPException(409, "في تحليل شغّال بالفعل")
        JOB.update(running=True, started=dt.datetime.now(TZ).isoformat(), finished=None, log="",
                   range=[str(date_from), str(date_to)])
    threading.Thread(target=_run_job, args=(str(date_from), str(date_to)), daemon=True).start()
    return JOB


@app.get("/api/analyze/status")
def analyze_status():
    return JOB


@app.get("/api/analysis")
def analysis(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    """نتايج التحليل للمكالمات في الفترة، مربوطة بالمكالمة"""
    out = []
    for c in calls(date_from, date_to):
        if not c["analyzed"]:
            continue
        row = _recording_row(c["id"])
        f = ANALYSIS_DIR / (stem(row["recordingfile"]) + ".json")
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append(dict(call=c, analysis=r.get("analysis", {}),
                        transcript=r.get("transcript", {}).get("lines", [])))
    return out


# ---------------- page ----------------
@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.exception_handler(pymysql.MySQLError)
def db_error(_, exc):
    return JSONResponse({"detail": f"خطأ في قاعدة بيانات السنترال: {exc}"}, status_code=500)
