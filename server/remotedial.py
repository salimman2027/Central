"""اتصل من شريحتي وأنا على اللاب: اللاب بيبعت الرقم، وتطبيق الموبايل (نفس التحويلة) بيستناه
بـ long-poll ويتصل بيه من الشريحة على طول. مفيش تخزين — كله في الذاكرة (أوامر بتعيش دقيقة)."""
import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
HDR = {"Access-Control-Allow-Origin": "*"}
CTX = {"check_ext": lambda e, p: False}
Q: dict = {}          # ext → [ {id, number, name, at} ]
EV: dict = {}         # ext → asyncio.Event
SEEN: dict = {}       # ext → آخر مرة الموبايل كان مستني (عشان اللاب يعرف إن فيه موبايل جاهز)
TTL = 60


def _ev(ext: str) -> asyncio.Event:
    if ext not in EV:
        EV[ext] = asyncio.Event()
    return EV[ext]


async def _auth(request: Request):
    b = await request.json()
    ext, pw = str(b.get("ext") or ""), str(b.get("password") or "")
    if not ext.isdigit() or not CTX["check_ext"](ext, pw):
        return None, b
    return ext, b


@router.post("/app/rdial/send")
async def send(request: Request):
    ext, b = await _auth(request)
    if not ext:
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    num = "".join(ch for ch in str(b.get("number") or "") if ch.isdigit() or ch == "+")[:20]
    if len(num) < 3:
        return JSONResponse({"detail": "الرقم غلط"}, 400, headers=HDR)
    if time.time() - SEEN.get(ext, 0) > 45:
        return JSONResponse({"detail": "الموبايل مش متصل — افتح تطبيق NQ Phone على موبايلك"}, 409, headers=HDR)
    item = {"id": f"{int(time.time()*1000)}", "number": num, "name": str(b.get("name") or "")[:80], "at": time.time()}
    Q.setdefault(ext, []).append(item)
    _ev(ext).set()
    return JSONResponse({"ok": True, "id": item["id"]}, headers=HDR)


@router.post("/app/rdial/wait")
async def wait(request: Request):
    """الموبايل بيستنى لحد 25 ثانية؛ لو جه أمر بيرجع على طول"""
    ext, b = await _auth(request)
    if not ext:
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    SEEN[ext] = time.time()
    ev = _ev(ext)
    if not [x for x in Q.get(ext, []) if time.time() - x["at"] < TTL]:
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=25)
        except asyncio.TimeoutError:
            pass
    SEEN[ext] = time.time()
    items = [x for x in Q.pop(ext, []) if time.time() - x["at"] < TTL]
    ev.clear()
    return JSONResponse({"items": items}, headers=HDR)


@router.post("/app/rdial/status")
async def status(request: Request):
    ext, b = await _auth(request)
    if not ext:
        return JSONResponse({"detail": "unauthorized"}, 401, headers=HDR)
    return JSONResponse({"phone": time.time() - SEEN.get(ext, 0) <= 45}, headers=HDR)
