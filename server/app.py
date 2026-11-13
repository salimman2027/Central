"""
NQ Calls — خدمة لوحة المكالمات على سيرفر السنترال
  - تقرا سجل المكالمات (CDR) من قاعدة بيانات FreePBX
  - تشغّل التسجيلات
  - تشغّل تحليل الذكاء الاصطناعي لما يتطلب (أو أسبوعيًا من cron)
بتشتغل على 127.0.0.1:8090 وApache بيوصّلها على https://<host>/calls/ ومعاها باسورد.
"""
import datetime as dt
import hmac
import json
import shutil
import time
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
CONF_DIR = Path(os.environ.get("NQ_CONF", "/etc/nq-calls"))
DATA_DIR = Path(os.environ.get("NQ_DATA", "/var/lib/nq-calls"))
ANALYSIS_DIR = DATA_DIR / "analysis"
NOTES_DIR = DATA_DIR / "notes"
SUP_FILE = DATA_DIR / "supervisor-notes.json"
SIM_DIR = DATA_DIR / "mobile"            # تسجيلات مكالمات الشريحة اللي التطبيق بيرفعها
SIM_INDEX = SIM_DIR / "index.jsonl"
SIM_LOCK = threading.Lock()
AUDIO_OK = {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".webm", ".aac", ".amr", ".3gp", ".opus", ".flac"}
try:
    SUP_NOTES = json.loads(SUP_FILE.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    SUP_NOTES = {}
MONITOR = Path(os.environ.get("NQ_MONITOR", "/var/spool/asterisk/monitor"))
TZ = ZoneInfo(os.environ.get("NQ_TZ", "Asia/Riyadh"))
TRUNK = "PJSIP/" + os.environ.get("NQ_TRUNK", "dinastar1")

app = FastAPI(title="NQ Calls", docs_url=None, redoc_url=None)


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api/") and "recording" not in request.url.path:
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------- DB ----------------
def db_conf() -> dict:
    """بيانات الدخول من /etc/freepbx.conf (نفس اللي FreePBX بيستخدمها)"""
    if os.environ.get("NQ_DB_USER"):
        return dict(host=os.environ.get("NQ_DB_HOST", "localhost"), user=os.environ["NQ_DB_USER"],
                    password=os.environ.get("NQ_DB_PASS", ""))
    txt = Path("/etc/freepbx.conf").read_text()
    def get(k, default=""):
        m = re.search(rf"""\[\s*['"]{k}['"]\s*\]\s*=\s*['"]([^'"]*)['"]""", txt)
        return m.group(1) if m else default
    return dict(host=get("AMPDBHOST", "localhost"), user=get("AMPDBUSER", "freepbxuser"), password=get("AMPDBPASS"))


def query(sql: str, args: tuple, database: str | None = None) -> list:
    c = db_conf()
    con = pymysql.connect(host=c["host"], user=c["user"], password=c["password"],
                          database=database or os.environ.get("NQ_CDR_DB", "asteriskcdrdb"),
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


# ---------------- CRM notes (from the phone app) ----------------
def _digits(n: str) -> str:
    return re.sub(r"\D", "", n or "")[-9:]


def load_notes(d_from: dt.date, d_to: dt.date) -> list:
    out, m = [], dt.date(d_from.year, d_from.month, 1)
    while m <= d_to:
        f = NOTES_DIR / f"{m:%Y-%m}.jsonl"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def match_note(notes: list, ext: int, number: str, t: dt.datetime):
    best, best_d = None, 15 * 60
    for n in notes:
        if str(n.get("ext")) != str(ext) or _digits(n.get("number")) != _digits(number):
            continue
        try:
            nt = dt.datetime.fromisoformat(n["started"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        d = abs((nt - t).total_seconds())
        if d < best_d:
            best, best_d = n, d
    if not best:
        return None
    return {k: best.get(k) for k in ("customer", "outcome", "summary", "followUp", "savedAt")}


FAILS: dict = {}


def check_ext_password(ext: str, password: str) -> bool:
    if not re.fullmatch(r"\d{2,6}", ext or ""):
        return False
    now = time.time()
    FAILS[ext] = [t for t in FAILS.get(ext, []) if now - t < 900]
    if len(FAILS[ext]) >= 10:
        raise HTTPException(429, "محاولات كتير، حاول بعد شوية")
    rows = query("SELECT data FROM sip WHERE id=%s AND keyword='secret' LIMIT 1", (ext,),
                 database=os.environ.get("NQ_PBX_DB", "asterisk"))
    ok = bool(rows) and hmac.compare_digest(str(rows[0]["data"]), password or "")
    if not ok:
        FAILS[ext].append(now)
    return ok


@app.post("/app/notes")
async def app_notes(request: Request):
    """التطبيق بيبعت ملخص المكالمة هنا (برقم التحويلة وباسوردها)"""
    hdr = {"Access-Control-Allow-Origin": "*"}
    try:
        body = json.loads(await request.body())
        ext, pw, note = str(body["ext"]), str(body["password"]), dict(body["note"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"detail": "bad request"}, 400, headers=hdr)
    if not check_ext_password(ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=hdr)
    clean = {k: str(note.get(k) or "")[:4000] for k in
             ("number", "direction", "started", "customer", "outcome", "summary", "followUp", "savedAt")}
    clean["duration"] = int(note.get("duration") or 0)
    clean["ext"] = ext
    clean["received"] = dt.datetime.now(dt.timezone.utc).isoformat()
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    with open(NOTES_DIR / f"{dt.date.today():%Y-%m}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(clean, ensure_ascii=False) + "\n")
    return JSONResponse({"ok": True}, headers=hdr)


INSIGHT_PROMPT = """أنت مستشار مبيعات خبير لشركة "الجودة الوطنية لتقنية المعلومات" (NQ) في السعودية.
موظف مبيعات كتب ملخص مكالمة مع عميل. حلّل الملخص وتوقّع موقف العميل. متخمّنش حاجة مش مكتوبة.

بيانات المكالمة: {meta}
ملخص الموظف:
<summary>
{summary}
</summary>

النهارده {today}.
رجّع JSON بس (من غير أي كلام قبله أو بعده) بالشكل ده:
{{
  "temperature": "ساخن | دافئ | بارد",
  "buy_probability": 0,
  "expectation": "توقّعك لموقف العميل في جملتين بالعامية المصرية البسيطة",
  "next_action": "أهم خطوة جاية للموظف، محددة وعملية",
  "followup_date": "YYYY-MM-DD أو null",
  "risks": ["خطر أو اعتراض محتمل"],
  "suggested_message": "رسالة واتساب قصيرة مهذبة يبعتها الموظف للعميل للمتابعة"
}}"""


@app.post("/app/upload")
async def app_upload(request: Request):
    """التطبيق بيرفع تسجيل مكالمة الشريحة (الملف في الـ body والبيانات في الهيدرز)"""
    h = request.headers
    ext, pw = h.get("x-ext", ""), h.get("x-pass", "")
    if not check_ext_password(ext, pw):
        raise HTTPException(401, "رقم التحويلة أو الباسورد غلط")
    number = re.sub(r"[^\d+]", "", h.get("x-number", ""))[:20] or "unknown"
    try:
        start_ms = int(h.get("x-start", "0")); duration = int(h.get("x-duration", "0"))
    except ValueError:
        raise HTTPException(400, "بيانات غلط")
    direction = "in" if h.get("x-dir") == "in" else "out"
    suffix = Path(h.get("x-filename", "rec.m4a")).suffix.lower()
    if suffix not in AUDIO_OK:
        raise HTTPException(400, "نوع الملف مش صوت")
    cid = f"sim-{ext}-{start_ms}"
    if cid in load_sim_calls():
        return {"ok": True, "id": cid, "dup": True}
    body = await request.body()
    if not body or len(body) > 60 * 1024 * 1024:
        raise HTTPException(413, "الملف فاضي أو كبير جدًا")
    t = dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc)
    rel = Path(f"{t:%Y}/{t:%m}") / f"{direction}-{number}-{ext}-{t:%Y%m%d}-{t:%H%M%S}-sim{start_ms}{suffix}"
    dest = SIM_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    if suffix in {".amr", ".3gp"} and shutil.which("ffmpeg"):  # صيغ مش كل خدمات التحويل بتقبلها
        mp3 = dest.with_suffix(".mp3")
        if subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dest), str(mp3)]).returncode == 0:
            dest.unlink(missing_ok=True); dest = mp3; rel = rel.with_suffix(".mp3")
    rec = dict(id=cid, ext=ext, number=number, start=start_ms, duration=duration, dir=direction,
               file=str(rel), uploaded=dt.datetime.now(TZ).isoformat())
    with SIM_LOCK, open(SIM_INDEX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "id": cid}


@app.post("/app/insight")
async def app_insight(request: Request):
    """تحليل سريع لملخص مكالمة واحدة (التطبيق بيطلبه بعد المكالمة)"""
    hdr = {"Access-Control-Allow-Origin": "*"}
    try:
        body = json.loads(await request.body())
        ext, pw, note = str(body["ext"]), str(body["password"]), dict(body["note"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"detail": "bad request"}, 400, headers=hdr)
    if not check_ext_password(ext, pw):
        return JSONResponse({"detail": "رقم التحويلة أو الباسورد غلط"}, 401, headers=hdr)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"detail": "مفتاح Claude API مش متضاف على السيرفر"}, 503, headers=hdr)
    summary = str(note.get("summary") or "")[:4000].strip()
    if not summary:
        return JSONResponse({"detail": "الملخص فاضي"}, 400, headers=hdr)
    meta = {k: note.get(k) for k in ("customer", "outcome", "direction", "duration")}
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=os.environ.get("NQ_CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=800,
            messages=[{"role": "user", "content": INSIGHT_PROMPT.format(
                meta=json.dumps(meta, ensure_ascii=False), summary=summary, today=dt.date.today().isoformat())}])
        raw = msg.content[0].text
        out = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001
        m = str(e)
        if "authentication_error" in m or "x-api-key" in m:
            m = "مفتاح Claude API على السيرفر غلط — المدير يكتب على السيرفر: sudo nq-setkey"
        elif "credit balance" in m:
            m = "رصيد حساب Anthropic خلص"
        return JSONResponse({"detail": f"فشل التحليل: {m}"}, 502, headers=hdr)
    return JSONResponse(out, headers=hdr)


@app.get("/api/notes")
def notes(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    return load_notes(date_from, date_to)


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

    notes_all = load_notes(date_from - dt.timedelta(days=1), date_to + dt.timedelta(days=1))
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
            note=match_note(notes_all, ext, main["src"] if inbound else strip_prefix(main["dst"] or "", ext), t),
            hasRecording=bool(rec) and main["disposition"] == "ANSWERED",
            analyzed=bool(rec) and (ANALYSIS_DIR / (stem(rec) + ".json")).exists(),
            rec=stem(rec) if rec else "",
            transcribed=bool(rec) and ((ANALYSIS_DIR / (stem(rec) + ".tr.json")).exists()
                                       or (ANALYSIS_DIR / (stem(rec) + ".json")).exists()),
            supNote=(SUP_NOTES.get(main["uniqueid"]) or {}).get("text", ""),
        ))
    # مكالمات الشريحة اللي اترفعت من التطبيق
    for r in load_sim_calls().values():
        t = dt.datetime.fromtimestamp(r["start"] / 1000, dt.timezone.utc)
        if not (start <= t.replace(tzinfo=None) < end):
            continue
        st = Path(r["file"]).stem
        out.append(dict(
            id=r["id"], time=t.isoformat(), ext=int(r["ext"]), dir=r["dir"], status="تم الرد",
            talkSec=int(r.get("duration") or 0), ringSec=0, number=r["number"], customer=None,
            note=match_note(notes_all, int(r["ext"]), r["number"], t), source="sim",
            hasRecording=True, rec=st,
            analyzed=(ANALYSIS_DIR / (st + ".json")).exists(),
            transcribed=(ANALYSIS_DIR / (st + ".tr.json")).exists() or (ANALYSIS_DIR / (st + ".json")).exists(),
            supNote=(SUP_NOTES.get(r["id"]) or {}).get("text", ""),
        ))
    out.sort(key=lambda c: c["time"], reverse=True)
    return out


def load_sim_calls() -> dict:
    out = {}
    try:
        with open(SIM_INDEX, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    out[r["id"]] = r
                except Exception:  # noqa: BLE001
                    pass
    except FileNotFoundError:
        pass
    return out


def rec_file(call_id: str) -> Path:
    """مسار ملف التسجيل لأي مكالمة — سنترال أو شريحة"""
    if call_id.startswith("sim-"):
        r = load_sim_calls().get(call_id)
        p = SIM_DIR / r["file"] if r else None
        if not p or not p.exists():
            raise HTTPException(404, "ملف التسجيل مش موجود على السيرفر")
        return p
    return _recording_path(_recording_row(call_id))


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
    p = rec_file(call_id)
    mt = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
          ".webm": "audio/webm", ".aac": "audio/aac", ".amr": "audio/amr", ".3gp": "audio/3gpp", ".flac": "audio/flac"}
    return FileResponse(p, media_type=mt.get(p.suffix.lower(), "audio/wav"), filename=p.name if download else None)


# ---------------- AI analysis ----------------
JOB = {"running": False, "started": None, "finished": None, "log": "", "range": None}
JOB_LOCK = threading.Lock()


def _run_job(args: list):
    cmd = ["nice", "-n", "19", sys.executable, str(HERE / "analyzer.py"), *args]
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
async def analyze(request: Request, date_from: dt.date | None = Query(None, alias="from"),
                  date_to: dt.date | None = Query(None, alias="to")):
    """يحلّل مكالمات محددة (JSON: {"ids": [...]}) أو كل مكالمات الفترة"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "مفتاح Claude API مش متضاف على السيرفر")
    import importlib.util
    if not os.environ.get("OPENAI_API_KEY") and not importlib.util.find_spec("faster_whisper"):
        raise HTTPException(400, "تحليل التسجيلات (المرحلة 2) لسه مش متركّب على السيرفر")
    ids, body = [], None
    try:
        body = await request.json()
        ids = [str(i) for i in (body or {}).get("ids", [])][:500]
    except Exception:  # noqa: BLE001
        pass
    if ids:
        files = []
        for i in ids:
            try:
                files.append(str(rec_file(i)))
            except HTTPException:
                pass
        if not files:
            raise HTTPException(404, "المكالمات المختارة مفيهاش تسجيلات على السيرفر")
        mode = (body or {}).get("mode") if isinstance(body, dict) else None
        if mode == "transcript":
            args, rng = ["--transcript-only", "--redo", "--min-sec", "0", *files], [f"تحويل {len(files)} مكالمة لنص", ""]
        else:
            args, rng = ["--redo", "--min-sec", "0", *files], [f"{len(files)} مكالمة", ""]
    elif date_from and date_to:
        args, rng = ["--from", str(date_from), "--to", str(date_to), "--min-sec", "10"], [str(date_from), str(date_to)]
    else:
        raise HTTPException(400, "اختار مكالمات أو فترة")
    with JOB_LOCK:
        if JOB["running"]:
            raise HTTPException(409, "في تحليل شغّال بالفعل")
        JOB.update(running=True, started=dt.datetime.now(TZ).isoformat(), finished=None, log="", range=rng)
        (ANALYSIS_DIR / "progress.json").unlink(missing_ok=True)
    threading.Thread(target=_run_job, args=(args,), daemon=True).start()
    return JOB


@app.get("/api/analyze/status")
def analyze_status():
    prog = None
    try:
        prog = json.loads((ANALYSIS_DIR / "progress.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {**JOB, "progress": prog}


@app.get("/api/analysis")
def analysis(date_from: dt.date = Query(alias="from"), date_to: dt.date = Query(alias="to")):
    """نتايج التحليل للمكالمات في الفترة، مربوطة بالمكالمة"""
    out = []
    for c in calls(date_from, date_to):
        if not c["analyzed"]:
            continue
        f = ANALYSIS_DIR / (c["rec"] + ".json")
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append(dict(call=c, analysis=r.get("analysis", {}),
                        transcript=r.get("transcript", {}).get("lines", [])))
    return out


@app.get("/api/call/{call_id}")
def call_detail(call_id: str):
    """النص والتحليل وملاحظة المدير لمكالمة واحدة"""
    out = {"transcript": [], "analysis": None, "supNote": (SUP_NOTES.get(call_id) or {}).get("text", "")}
    try:
        st = rec_file(call_id).stem
    except HTTPException:
        return out
    for f in (ANALYSIS_DIR / (st + ".json"), ANALYSIS_DIR / (st + ".tr.json")):
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if "analysis" in d:
                out["analysis"] = d["analysis"]
                out["transcript"] = d.get("transcript", {}).get("lines", [])
                break
            out["transcript"] = d.get("lines", [])
    return out


SUP_LOCK = threading.Lock()


@app.post("/api/supnote")
async def save_supnote(request: Request):
    """ملاحظة المدير على مكالمة"""
    body = await request.json()
    cid, text = str(body.get("id", ""))[:64], str(body.get("text", ""))[:4000]
    if not cid:
        raise HTTPException(400, "id مطلوب")
    with SUP_LOCK:
        if text.strip():
            SUP_NOTES[cid] = {"text": text.strip(), "at": dt.datetime.now(TZ).isoformat()}
        else:
            SUP_NOTES.pop(cid, None)
        tmp = SUP_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(SUP_NOTES, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SUP_FILE)
    return {"ok": True}


# ---------------- page ----------------
@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.exception_handler(pymysql.MySQLError)
def db_error(_, exc):
    return JSONResponse({"detail": f"خطأ في قاعدة بيانات السنترال: {exc}"}, status_code=500)
