"""بنود تقييم مكالمات التيلي سيلز — المدير يقدر يعدّلها من اللوحة (الأسماء والأوزان وإيه اللي الـ AI يدور عليه)."""
import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("NQ_DATA", "/var/lib/nq-calls"))
RUBRIC_FILE = DATA_DIR / "rubric.json"

DEFAULT = [
    {"key": "opening", "name": "الافتتاحية وأول انطباع", "weight": 10,
     "look_for": "هل السيلز عرّف نفسه والشركة بوضوح؟ هل اتأكد إنه بيكلم الشخص الصح وإن الوقت مناسب؟ وهل شد انتباه العميل في أول 30 ثانية؟"},
    {"key": "discovery", "name": "اكتشاف الاحتياج", "weight": 15,
     "look_for": "هل سأل أسئلة مفتوحة زي \"إيه اللي بتستخدمه دلوقتي؟\" و\"إيه أكتر حاجة مضايقاك؟\"؟ وهل وصل للمشكلة الحقيقية قبل ما يبدأ يعرض؟"},
    {"key": "listening", "name": "الاستماع الفعّال", "weight": 10,
     "look_for": "نسبة كلام السيلز للعميل (المثالي حوالي 40:60 لصالح العميل). هل قاطع العميل؟ وهل لخّص كلامه وأعاده عليه؟"},
    {"key": "value", "name": "عرض المنتج وربطه بالقيمة", "weight": 15,
     "look_for": "هل ربط المميزات بمشكلة العميل نفسها؟ وهل كلامه عن المنتج والأسعار دقيق ومفيهوش وعود مش حقيقية؟"},
    {"key": "objections", "name": "التعامل مع الاعتراضات", "weight": 15,
     "look_for": "إيه الاعتراضات اللي ظهرت (السعر، الوقت، \"هفكر\"، المنافس)؟ هل فهم الاعتراض الأول قبل ما يرد؟ ونتيجة كل اعتراض: اتحل ولا لأ؟"},
    {"key": "closing", "name": "محاولة الإغلاق والخطوة الجاية", "weight": 15,
     "look_for": "هل طلب البيع أو حجز ميعاد أو ديمو بشكل صريح؟ وهل المكالمة خلصت بخطوة واضحة بتاريخ ووقت محددين؟"},
    {"key": "sentiment", "name": "النبرة والمشاعر", "weight": 5,
     "look_for": "مشاعر العميل في البداية مقارنة بالنهاية (إيجابي، محايد، سلبي). ومستوى الحماس والثقة والهدوء في كلام السيلز."},
    {"key": "professionalism", "name": "اللغة والاحترافية", "weight": 5,
     "look_for": "وضوح الكلام، والبعد عن كلمات الحشو والمصطلحات المعقدة. ومفيش أي لفظ غير لائق أو ضغط زيادة على العميل."},
    {"key": "compliance", "name": "الالتزام بالسكريبت والكومبلاينس", "weight": 5,
     "look_for": "هل قال النقاط الإلزامية (الشروط والأسعار وسياسة الاسترجاع)؟ وهل في أي معلومة غلط أو مخالفة؟"},
    {"key": "outcome", "name": "نتيجة المكالمة ومؤشرات الشراء", "weight": 5,
     "look_for": "النتيجة النهائية: بيع، أو ميعاد، أو متابعة، أو رفض. وإشارات اهتمام العميل زي إنه يسأل عن السعر أو التسليم أو الدفع، ونسبة احتمال إن الصفقة تتقفل."},
]


def load() -> list:
    try:
        r = json.loads(RUBRIC_FILE.read_text(encoding="utf-8"))
        if isinstance(r, list) and r:
            return r
    except Exception:  # noqa: BLE001
        pass
    return [dict(x) for x in DEFAULT]


def validate(items) -> list:
    if not isinstance(items, list) or not (1 <= len(items) <= 20):
        raise ValueError("لازم من بند واحد لـ 20 بند")
    out, keys = [], set()
    for i, it in enumerate(items):
        name = str(it.get("name", "")).strip()[:80]
        look = str(it.get("look_for", "")).strip()[:600]
        try:
            w = int(it.get("weight", 0))
        except (TypeError, ValueError):
            raise ValueError(f"وزن البند {i + 1} لازم يبقى رقم")
        if not name:
            raise ValueError(f"البند {i + 1} من غير اسم")
        if w < 0 or w > 100:
            raise ValueError(f"وزن \"{name}\" لازم بين 0 و100")
        key = str(it.get("key") or "").strip() or f"c{i + 1}"
        while key in keys:
            key += "_"
        keys.add(key)
        out.append({"key": key, "name": name, "weight": w, "look_for": look})
    total = sum(x["weight"] for x in out)
    if total != 100:
        raise ValueError(f"مجموع الأوزان لازم يبقى 100 (دلوقتي {total})")
    return out


def save(items) -> list:
    clean = validate(items)
    RUBRIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUBRIC_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(RUBRIC_FILE)
    return clean


def weighted_total(rubric: list, criteria: list) -> int:
    """الدرجة الكلية من 100 = مجموع (درجة البند من 10 × وزنه ÷ 10)"""
    by = {c.get("key"): c for c in criteria or []}
    tot = 0.0
    for r in rubric:
        try:
            sc = float(by.get(r["key"], {}).get("score", 0))
        except (TypeError, ValueError):
            sc = 0
        tot += max(0, min(10, sc)) * r["weight"] / 10
    return round(tot)
