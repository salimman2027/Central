"""أنواع: مشاكل العملاء، أخطاء الموظفين، وأسباب عدم الشراء — المدير يقدر يعدّلها من اللوحة."""
import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("NQ_DATA", "/var/lib/nq-calls"))
FILE = DATA_DIR / "taxonomy.json"

LABELS = {
    "customer_pains": "مشاكل العملاء",
    "agent_issues": "مشاكل الموظف في المكالمة",
    "no_buy": "أسباب عدم الشراء",
}

DEFAULT = {
    "customer_pains": [
        "السعر أو التكلفة عالية", "البرنامج الحالي مش مناسب", "صعوبة استخدام البرنامج", "ضعف الدعم الفني",
        "التقارير والحسابات", "الفوترة الإلكترونية والزكاة", "المخزون والجرد", "تعدد الفروع",
        "الربط مع أنظمة تانية", "بطء أو أعطال", "أخرى",
    ],
    "agent_issues": [
        "مسألش عن الاحتياج", "قبل الرفض بسرعة", "مطلبش ميعاد أو خطوة جاية", "عرض المنتج من غير ربط بمشكلة العميل",
        "قاطع العميل أو اتكلم كتير", "معلومات غلط أو ناقصة", "افتتاحية ضعيفة", "ضغط زيادة على العميل",
        "ما تعاملش مع الاعتراض", "أخرى",
    ],
    "no_buy": [
        "السعر", "مش الوقت المناسب (جرد / نهاية سنة / انشغال)", "عنده برنامج حالي أو متعاقد", "مش صاحب القرار",
        "مش محتاج", "بيقارن بمنافس", "مفيش ميزانية", "عايز يفكر", "رقم غلط أو مش مهتم خالص", "أخرى",
    ],
}


def load() -> dict:
    try:
        t = json.loads(FILE.read_text(encoding="utf-8"))
        return {k: list(t.get(k) or DEFAULT[k]) for k in DEFAULT}
    except Exception:  # noqa: BLE001
        return {k: list(v) for k, v in DEFAULT.items()}


def save(t: dict) -> dict:
    clean = {}
    for k in DEFAULT:
        items = [str(x).strip()[:80] for x in (t.get(k) or []) if str(x).strip()]
        items = list(dict.fromkeys(items))
        if not items:
            raise ValueError(f"\"{LABELS[k]}\" لازم يبقى فيها نوع واحد على الأقل")
        if len(items) > 40:
            raise ValueError(f"\"{LABELS[k]}\" أقصى حاجة 40 نوع")
        if "أخرى" not in items:
            items.append("أخرى")
        clean[k] = items
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)
    return clean
