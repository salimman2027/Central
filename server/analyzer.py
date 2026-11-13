#!/usr/bin/env python3
"""
NQ Call AI — نموذج تجريبي لتحليل تسجيلات المكالمات
  1) يحوّل تسجيل المكالمة لنص (faster-whisper، عربي، بيشتغل على السيرفر نفسه)
  2) يبعت النص لـ Claude ويرجّع: ملخص، مزاج العميل، نتيجة المكالمة، تقييم الموظف، مهام متابعة
  3) يحفظ النتيجة JSON + صفحة HTML تفتحها في المتصفح

الاستخدام:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 analyze_call.py /var/spool/asterisk/monitor/2026/11/12/out-0505...wav
  python3 analyze_call.py --latest 5                    # آخر 5 تسجيلات
  python3 analyze_call.py --from 2026-11-01 --to 2026-11-07
  python3 analyze_call.py --last-week                   # التشغيل الأسبوعي (السبت)
  اللي اتحلّل قبل كده بيتخطّى، والمكالمات الأقصر من 30 ثانية بتتخطّى (--min-sec)
"""
import argparse, glob, html, json, os, sys, time
from pathlib import Path

MONITOR = "/var/spool/asterisk/monitor"
OUT_DIR = Path(os.environ.get("NQ_AI_OUT", Path.home() / "call-ai"))
WHISPER_MODEL = os.environ.get("NQ_WHISPER_MODEL", "large-v3-turbo")   # small / medium / large-v3-turbo
CLAUDE_MODEL = os.environ.get("NQ_CLAUDE_MODEL", "claude-sonnet-5-5")

# نقط تقييم الموظف — عدّلها حسب سكريبت البيع عندكم
CHECKLIST = [
    "عرّف بنفسه وباسم الشركة",
    "سأل عن احتياج العميل قبل ما يعرض",
    "شرح المنتج أو العرض بوضوح",
    "رد على اعتراضات العميل",
    "اتفق على خطوة جاية (ميعاد / عرض سعر / متابعة)",
    "كان مهذّب ومحترف طول المكالمة",
]

PROMPT = """أنت محلل جودة مكالمات لشركة "الجودة الوطنية لتقنية المعلومات" (NQ) في السعودية.
ده نص مكالمة بين موظف مبيعات وعميل (النص متحوّل آليًا من الصوت، فممكن يكون فيه أخطاء — متخمّنش حاجة مش موجودة).

بيانات المكالمة: {meta}

النص:
<transcript>
{transcript}
</transcript>

رجّع JSON بس (من غير أي كلام قبله أو بعده) بالشكل ده بالظبط:
{{
  "summary": "ملخص المكالمة في سطرين بالعربي",
  "customer_sentiment": "راضي | محايد | متضايق",
  "sentiment_reason": "سبب قصير",
  "outcome": "بيع | مهتم | يحتاج متابعة | رفض | لم يكتمل",
  "outcome_reason": "سبب قصير",
  "customer_needs": ["احتياج 1", "..."],
  "objections": ["اعتراض 1", "..."],
  "checklist": [{{"item": "...", "done": true, "evidence": "جملة قصيرة من المكالمة أو null"}}],
  "agent_score": 0,
  "coaching_tip": "نصيحة واحدة عملية للموظف",
  "follow_up_tasks": [{{"task": "...", "due": "ميعاد لو اتذكر أو null"}}]
}}
agent_score من 0 لـ 100 حسب نقط التقييم دي: {checklist}"""


_MODEL = None


PROMPT_HINT = "مكالمة تليفون بين موظف مبيعات في شركة الجودة الوطنية لتقنية المعلومات وعميل، باللهجة السعودية أو المصرية."


def _segments(wav: str, vad: bool):
    kw = dict(language="ar", beam_size=5, best_of=5, condition_on_previous_text=False,
              initial_prompt=PROMPT_HINT, no_speech_threshold=0.6, compression_ratio_threshold=2.2)
    if vad:
        kw.update(vad_filter=True, vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300})
    segs, info = _MODEL.transcribe(wav, **kw)
    lines = []
    for s in segs:
        t = s.text.strip()
        # بنشيل الكلام المتألّف (لما النموذج مش متأكد أو المقطع سكوت)
        if not t or s.no_speech_prob > 0.6 or s.avg_logprob < -1.1:
            continue
        if lines and t == lines[-1]["text"]:
            continue
        lines.append({"start": round(s.start, 1), "end": round(s.end, 1), "text": t})
    return lines, info


OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_STT = os.environ.get("NQ_OPENAI_STT_MODEL", "gpt-4o-transcribe")


def transcribe_openai(wav: str) -> dict:
    """تحويل لنص على OpenAI — أدق بكتير في العربي وصوت التليفون"""
    import re
    import requests
    with open(wav, "rb") as fh:
        r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                          data={"model": OPENAI_STT, "language": "ar", "response_format": "json",
                                "prompt": PROMPT_HINT + " اكتب الكلام زي ما اتقال بالظبط بالعامية."},
                          files={"file": (Path(wav).name, fh, "audio/wav")}, timeout=300)
    if r.status_code == 401:
        raise RuntimeError("مفتاح OpenAI غلط — على السيرفر اكتب: sudo nq-setkey openai")
    if r.status_code == 429:
        raise RuntimeError("OpenAI: الرصيد خلص أو طلبات كتير — اشحن من platform.openai.com")
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:200]}")
    text = (r.json().get("text") or "").strip()
    parts = [p.strip() for p in re.split(r"(?<=[.!؟?،])\s+|\n+", text) if p.strip()]
    lines = [{"start": None, "end": None, "text": p} for p in parts]
    return {"duration": 0, "lines": lines, "model": "openai:" + OPENAI_STT}


def transcribe(wav: str) -> dict:
    if OPENAI_KEY:
        return transcribe_openai(wav)
    global _MODEL
    if _MODEL is None:  # النموذج بيتحمّل مرة واحدة بس لكل التسجيلات
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    try:
        lines, info = _segments(wav, vad=True)
    except TypeError:  # نسخ faster-whisper بتختلف في إعدادات فلتر السكوت
        lines, info = [], None
    if not lines:  # صوت التليفون ساعات فلتر السكوت بيشيله كله — نجرّب من غيره
        lines, info = _segments(wav, vad=False)
    return {"duration": round(info.duration) if info else 0, "lines": lines, "model": WHISPER_MODEL}


def analyze(transcript_lines: list, meta: dict) -> dict:
    import anthropic
    text = "\n".join((f"[{l['start']:>6}s] " if l.get("start") is not None else "- ") + l["text"] for l in transcript_lines)
    msg = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT.format(
            meta=json.dumps(meta, ensure_ascii=False), transcript=text,
            checklist=json.dumps(CHECKLIST, ensure_ascii=False))}],
    )
    raw = msg.content[0].text.strip()
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return json.loads(raw)


def meta_from_name(path: str) -> dict:
    # FreePBX: out-0505455943-100-20261112-141812-1794482330.51.wav
    p = Path(path).stem.split("-")
    m = {"file": Path(path).name}
    if len(p) >= 5:
        m.update(direction={"out": "صادرة", "in": "واردة", "external": "واردة"}.get(p[0], p[0]),
                 number=p[1], extension=p[2],
                 time=f"{p[3][:4]}-{p[3][4:6]}-{p[3][6:]} {p[4][:2]}:{p[4][2:4]} UTC")
    return m


def to_html(results: list) -> str:
    e = html.escape
    cards = []
    for r in results:
        a, m = r["analysis"], r["meta"]
        chk = "".join(
            f"<li class='{'ok' if c.get('done') else 'no'}'>{'✔' if c.get('done') else '✖'} {e(c.get('item',''))}"
            f"{'<small> — ' + e(c['evidence']) + '</small>' if c.get('evidence') else ''}</li>"
            for c in a.get("checklist", []))
        tasks = "".join(f"<li>{e(t.get('task',''))}{' — ' + e(t['due']) if t.get('due') else ''}</li>"
                        for t in a.get("follow_up_tasks", [])) or "<li>—</li>"
        tr = "".join(f"<div><span>{'' if l.get('start') is None else str(l['start']) + 's'}</span> {e(l['text'])}</div>" for l in r["transcript"]["lines"])
        cards.append(f"""
<section class="card">
  <header><b>{e(m.get('direction',''))} · تحويلة {e(m.get('extension','—'))} · {e(m.get('number','—'))}</b>
    <span>{e(m.get('time',''))} · {r['transcript']['duration']} ث</span></header>
  <div class="row">
    <div class="score">{a.get('agent_score','—')}<small>/100 تقييم الموظف</small></div>
    <div><span class="pill">{e(a.get('outcome',''))}</span> <span class="pill">{e(a.get('customer_sentiment',''))}</span>
      <p>{e(a.get('summary',''))}</p></div>
  </div>
  <h4>نقط التقييم</h4><ul class="chk">{chk}</ul>
  <h4>نصيحة للموظف</h4><p>{e(a.get('coaching_tip',''))}</p>
  <h4>مهام متابعة</h4><ul>{tasks}</ul>
  <details><summary>نص المكالمة</summary><div class="tr">{tr}</div></details>
</section>""")
    return f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NQ Call AI</title>
<style>
body{{font-family:"Segoe UI",Tahoma,sans-serif;background:#F3F7F7;color:#1C2B2A;margin:0;padding:20px}}
h1{{color:#0A5654;margin:0 0 4px}} .muted{{color:#6B7C7B;font-size:13px}}
.card{{background:#fff;border:1px solid #D5E2E1;border-radius:12px;padding:16px;margin:14px auto;max-width:900px}}
.card header{{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;border-bottom:1px solid #E8EEEE;padding-bottom:8px}}
.card header span{{color:#6B7C7B;font-size:13px}}
.row{{display:flex;gap:16px;align-items:center;margin-top:12px}}
.score{{font-size:40px;font-weight:800;color:#0F7C7A;text-align:center;min-width:110px}}
.score small{{display:block;font-size:12px;color:#6B7C7B;font-weight:400}}
.pill{{display:inline-block;background:#E7F3F2;color:#0F7C7A;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:600}}
h4{{color:#0A5654;margin:14px 0 6px}} ul{{margin:0;padding-inline-start:18px}}
.chk{{list-style:none;padding:0}} .chk li{{padding:3px 0}} .ok{{color:#1E9E5A}} .no{{color:#D64541}}
.chk small{{color:#6B7C7B}} .tr{{font-size:13px;line-height:1.8;max-height:320px;overflow:auto;background:#F7FBFB;padding:10px;border-radius:8px}}
.tr span{{color:#6B7C7B;font-family:monospace;direction:ltr;display:inline-block;min-width:48px}}
</style>
<h1>NQ Call AI — تحليل المكالمات</h1>
<div class="muted">نموذج تجريبي · النص متحوّل آليًا من الصوت وممكن يكون فيه أخطاء · {time.strftime('%Y-%m-%d %H:%M')}</div>
{''.join(cards)}
</html>"""


def recordings_between(d_from: str, d_to: str) -> list:
    """كل التسجيلات من يوم d_from لحد d_to (YYYY-MM-DD) — حسب فولدرات السنترال YYYY/MM/DD"""
    import datetime as dt
    a, b = dt.date.fromisoformat(d_from), dt.date.fromisoformat(d_to)
    out = []
    while a <= b:
        out += sorted(glob.glob(f"{MONITOR}/{a:%Y/%m/%d}/*.wav"))
        a += dt.timedelta(days=1)
    return out


def main():
    import datetime as dt
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--latest", type=int, default=0, help="حلّل آخر N تسجيلات")
    ap.add_argument("--from", dest="d_from", help="من يوم YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", help="لحد يوم YYYY-MM-DD (افتراضي: النهارده)")
    ap.add_argument("--last-week", action="store_true", help="آخر 7 أيام (للتشغيل الأسبوعي)")
    ap.add_argument("--min-sec", type=int, default=30, help="تجاهل التسجيلات الأقصر من كده بالثواني")
    ap.add_argument("--redo", action="store_true", help="حلّل تاني حتى لو اتحلّلت قبل كده")
    ap.add_argument("--transcript-only", action="store_true", help="حوّل لنص بس من غير تحليل")
    args = ap.parse_args()

    files = list(args.files)
    today = dt.date.today()
    if args.last_week:
        args.d_from, args.d_to = str(today - dt.timedelta(days=7)), str(today)
    if args.d_from:
        files += recordings_between(args.d_from, args.d_to or str(today))
    if args.latest:
        allw = sorted(glob.glob(f"{MONITOR}/**/*.wav", recursive=True), key=os.path.getmtime)
        files += allw[-args.latest:]
    if not files:
        sys.exit("مفيش تسجيلات. اكتب مسار ملف، أو --from 2026-11-01، أو --last-week، أو --latest 5")

    # تخطّي اللي اتحلّل قبل كده، والمكالمات القصيرة (صوت ~16KB في الثانية للـ wav 8kHz)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    before = len(files)
    if not args.redo:
        files = [f for f in files if not (OUT_DIR / (Path(f).stem + ".json")).exists()]
    files = [f for f in files if os.path.getsize(f) >= args.min_sec * 16000]
    print(f"هيتحلّل {len(files)} تسجيل (من {before}) — الباقي اتحلّل قبل كده أو أقصر من {args.min_sec} ثانية")
    if not files:
        return
    if not args.transcript_only and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("حط ANTHROPIC_API_KEY الأول: export ANTHROPIC_API_KEY=sk-ant-...")

    # تقدّم التحليل: اللوحة بتقراه كل كام ثانية وتعرض كل مكالمة فين
    prog = {"total": len(files), "done": 0, "current": None, "stage": "", "errors": {}, "skipped": []}
    pfile = OUT_DIR / "progress.json"

    def save_prog():
        tmp = pfile.with_suffix(".tmp")
        tmp.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        tmp.replace(pfile)

    save_prog()
    results = []
    for f in files:
        name = Path(f).stem
        prog.update(current=name, stage="transcribe"); save_prog()
        print(f"▶ {Path(f).name}: تحويل لنص...", flush=True)
        try:
            trf = OUT_DIR / (name + ".tr.json")
            t = None
            if trf.exists() and not (args.transcript_only and args.redo):  # اتحوّل لنص قبل كده بنفس النموذج — نوفّر الوقت
                try:
                    t = json.loads(trf.read_text(encoding="utf-8"))
                    if t.get("model") != ("openai:" + OPENAI_STT if OPENAI_KEY else WHISPER_MODEL):
                        t = None
                except Exception:  # noqa: BLE001
                    t = None
            if t is None or not t.get("lines"):
                t = transcribe(f)
                trf.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
            if args.transcript_only:
                prog["errors"].pop(name, None)
                if not t["lines"]:
                    prog["errors"][name] = "مفيش كلام واضح في التسجيل"
                print(f"   ✔ نص: {len(t['lines'])} سطر", flush=True)
                continue
            if not t["lines"]:
                print("   مفيش كلام في التسجيل، اتخطّى")
                prog["skipped"].append(name); prog["errors"][name] = "مفيش كلام واضح في التسجيل"
                continue
            meta = meta_from_name(f)
            prog["stage"] = "analyze"; save_prog()
            print("   تحليل...", flush=True)
            a = analyze(t["lines"], meta)
            r = {"meta": meta, "transcript": t, "analysis": a}
            (OUT_DIR / (name + ".json")).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append(r)
            print(f"   ✔ {a.get('outcome')} · {a.get('customer_sentiment')} · تقييم {a.get('agent_score')}/100")
        except Exception as e:  # noqa: BLE001  — مكالمة واحدة غلط متوقفش الباقي
            msg = str(e)
            if "authentication_error" in msg or "x-api-key" in msg:
                msg = "مفتاح Claude API غلط — على السيرفر اكتب: sudo nq-setkey"
            elif "credit balance" in msg:
                msg = "رصيد حساب Anthropic خلص — اشحن من console.anthropic.com"
            else:
                msg = f"{type(e).__name__}: {msg}"[:300]
            prog["errors"][name] = msg
            print(f"   ✖ {msg}", flush=True)
        finally:
            prog["done"] += 1; prog["current"] = None; prog["stage"] = ""; save_prog()
    out = OUT_DIR / f"report-{time.strftime('%Y-%m-%d')}.html"
    out.write_text(to_html(results), encoding="utf-8")
    print(f"\nالتقرير: {out}")


if __name__ == "__main__":
    main()
