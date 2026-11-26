"""البيانات الافتتاحية للمكالمات (أرصدة أول المدة)

المدير بيدخّل لكل مندوب أرقام مكالماته من قبل ما يشتغل على السيستم (صادرة، واردة، اترد عليها، …).
الأرقام دي بتتربط بتاريخ بداية المندوب (أول مكالمة ليه على السيستم — أو تاريخ المدير يختاره)،
وبتتضاف للوحة والتقارير والتارجت في أي فترة فيها التاريخ ده (يعني شهر البداية).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
CTX: dict = {"dir": None, "calls": None, "tz": None, "scope": None}
LOCK = threading.Lock()
FIELDS = ("out", "in", "ans", "no", "miss", "talk")   # talk بالثواني
_first_cache: dict = {"t": 0, "v": {}}


def _f() -> Path:
    return Path(CTX["dir"]) / "opening.json"


def load() -> dict:
    try:
        d = json.loads(_f().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict):
    p = _f()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def first_calls() -> dict:
    """أول مكالمة لكل تحويلة في آخر سنة (لتاريخ البداية الافتراضي) — بتتحسب مرة كل 10 دقايق"""
    if time.time() - _first_cache["t"] < 600 and _first_cache["v"]:
        return _first_cache["v"]
    out: dict = {}
    try:
        today = dt.datetime.now(CTX["tz"]).date()
        for c in CTX["calls"](today - dt.timedelta(days=365), today) or []:
            e = str(c.get("ext") or "")
            t = str(c.get("time") or c.get("start") or "")[:10]
            if e and re.fullmatch(r"\d{4}-\d{2}-\d{2}", t) and (e not in out or t < out[e]):
                out[e] = t
    except Exception:  # noqa: BLE001
        pass
    _first_cache.update(t=time.time(), v=out)
    return out


def totals(ext: str, a: dt.date, b: dt.date) -> dict | None:
    """أرقام المندوب الافتتاحية لو تاريخها جوه الفترة [a, b]"""
    r = load().get(str(ext))
    if not r:
        return None
    try:
        d = dt.date.fromisoformat(r.get("date") or "")
    except ValueError:
        return None
    return r if a <= d <= b else None


@router.get("/api/opening")
def get_opening():
    sc = CTX["scope"]() if CTX["scope"] else None
    items = load()
    if sc is not None:   # قائد الفريق يشوف فريقه بس
        items = {e: v for e, v in items.items() if e in sc["exts"]}
    return {"items": items, "first": first_calls()}


@router.put("/api/opening")
async def put_opening(request: Request):
    """المدير يحفظ (أو يمسح) الأرقام الافتتاحية لمندوب"""
    if CTX["scope"] and CTX["scope"]() is not None:
        raise HTTPException(403, "البيانات الافتتاحية للمدير بس")
    b = await request.json()
    ext = str(b.get("ext") or "")
    if not ext.isdigit():
        raise HTTPException(400, "التحويلة غلط")
    with LOCK:
        d = load()
        if b.get("delete"):
            d.pop(ext, None)
            _save(d)
            return {"ok": True, "ext": ext, "deleted": True}
        date = str(b.get("date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(400, "اختار تاريخ البداية")
        rec = {"date": date}
        try:
            for k in FIELDS:
                v = b.get(k)
                rec[k] = max(0, int(round(float(v or 0) * (60 if k == "talk" else 1))))   # talk بييجي بالدقايق
        except (TypeError, ValueError):
            raise HTTPException(400, "فيه رقم غلط")
        if rec["ans"] + rec["no"] + rec["miss"] > rec["out"] + rec["in"]:
            raise HTTPException(400, "مجموع (اترد + لم يرد + فائتة) أكبر من إجمالي المكالمات (صادرة + واردة)")
        rec["note"] = str(b.get("note") or "")[:200]
        rec["updated"] = dt.datetime.now(CTX["tz"]).isoformat(timespec="seconds")
        d[ext] = rec
        _save(d)
    return {"ok": True, "ext": ext, **rec}
