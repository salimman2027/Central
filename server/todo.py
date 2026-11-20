"""مهام اليوم للموظف (To-Do): مهام بيكتبها لنفسه، بتتحفظ على السيرفر عشان تبان على الموبايل واللاب.
كل موظف ليه ملف: {يوم: [{id, text, time, done, doneAt, carried}]}. المهام اللي ماخلصتش بتترحّل لليوم اللي بعده.
والمدير يشوف مهام فريقه من اللوحة (/api/todo)."""
import datetime as dt
import json
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
LOCK = threading.Lock()
HDR = {"Access-Control-Allow-Origin": "*"}
CTX = {"dir": Path("/var/lib/nq-calls"), "tz": dt.timezone(dt.timedelta(hours=3)),
       "check_ext": lambda e, p: False, "names": lambda: {}, "scope": lambda: None}
KEEP_DAYS = 60


def _f(ext: str) -> Path:
    d = CTX["dir"] / "todo"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{int(ext)}.json"


def _load(ext: str) -> dict:
    try:
        return json.loads(_f(ext).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(ext: str, data: dict):
    cut = (dt.datetime.now(CTX["tz"]).date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    data = {k: v for k, v in data.items() if k >= cut}
    tmp = _f(ext).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_f(ext))


def today() -> str:
    return dt.datetime.now(CTX["tz"]).date().isoformat()


def day_items(ext: str, data: dict, day: str) -> list:
    """مهام اليوم — ولو أول مرة النهارده: نرحّل اللي ماخلصش من آخر يوم قبله"""
    if day not in data and day == today():
        prev = sorted(k for k in data if k < day)
        carry = [dict(x, id=secrets.token_hex(4), carried=True, done=False, doneAt=None)
                 for x in (data[prev[-1]] if prev else []) if not x.get("done")]
        data[day] = carry
        _save(ext, data)
    return data.get(day, [])


def summary(ext: str, day: str | None = None) -> dict:
    items = _load(ext).get(day or today(), [])
    return {"total": len(items), "done": sum(1 for x in items if x.get("done"))}


@router.post("/app/todo")
async def app_todo(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not ext.isdigit() or not CTX["check_ext"](ext, pw):
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    day = str(b.get("date") or today())[:10]
    act = b.get("action") or "list"
    with LOCK:
        data = _load(ext)
        items = day_items(ext, data, day)
        if act == "add":
            text = str(b.get("text") or "").strip()[:300]
            if text:
                items.append({"id": secrets.token_hex(4), "text": text, "time": str(b.get("time") or "")[:5],
                              "done": False, "doneAt": None, "at": dt.datetime.now(dt.timezone.utc).isoformat()})
        elif act in ("toggle", "del", "edit"):
            x = next((x for x in items if x["id"] == b.get("id")), None)
            if x and act == "toggle":
                x["done"] = not x.get("done")
                x["doneAt"] = dt.datetime.now(dt.timezone.utc).isoformat() if x["done"] else None
            elif x and act == "del":
                items.remove(x)
            elif x and act == "edit":
                x["text"] = str(b.get("text") or x["text"]).strip()[:300]
                x["time"] = str(b.get("time") if b.get("time") is not None else x.get("time") or "")[:5]
        if act != "list":
            items.sort(key=lambda x: (x.get("done", False), x.get("time") or "99:99"))
            data[day] = items
            _save(ext, data)
    return JSONResponse({"date": day, "items": items}, headers=HDR)


@router.get("/api/todo")
def api_todo(date: str = ""):
    """للوحة: مهام كل الموظفين (أو فريق المدير بس) في يوم"""
    day = (date or today())[:10]
    names = CTX["names"]()
    sc = CTX["scope"]()
    allowed = set(sc["exts"]) if sc else None
    out = []
    d = CTX["dir"] / "todo"
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        ext = f.stem
        if allowed is not None and ext not in allowed:
            continue
        items = _load(ext).get(day, [])
        if items:
            out.append({"ext": ext, "name": names.get(ext, ext), "items": items,
                        "done": sum(1 for x in items if x.get("done")), "total": len(items)})
    return {"date": day, "reps": out}
