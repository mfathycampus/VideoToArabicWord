"""قياس دقة التفريغ العربي (WER / CER) مقابل نصوص مرجعية.

الاستخدام:
    python tools/measure_wer.py --eval-set eval_set.json --model large-v3

صيغة ملف التقييم: قائمة من
    {"file": "مسار الصوت", "norm": "النص المرجعي الصحيح"}

ملاحظة منهجية: WER يقاس على نص **مطبَّع** من الطرفين (بلا تشكيل ولا
ترقيم ولا تطويل)، وإلا قِسنا اختلافات إملائية لا أخطاء تعرّف.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
# علامات الترقيم العربية داخل نطاق ؀-ۿ فلا يكفي استثناء النطاق
_ARABIC_PUNCT = "،؛؟٪٫٬٭۔"
_PUNCT = re.compile(r"[^\w\s]|[" + _ARABIC_PUNCT + "]")


def normalize_for_scoring(text: str) -> str:
    """تطبيع تقييمي صارم — للقياس فقط، لا يُطبَّق على مخرج المستخدم.

    يوحّد الألف والياء والتاء المربوطة لأن اختلافها إملائي لا يمثل
    خطأ تعرّف على الكلام.
    """
    text = unicodedata.normalize("NFC", text)
    text = _DIACRITICS.sub("", text)
    text = text.replace("ـ", "")
    text = re.sub(r"[أإآٱ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = text.replace("ؤ", "و").replace("ئ", "ي")
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


def normalize_orthography_tolerant(text: str) -> str:
    """تطبيع أوسع يوحّد الغموض الإملائي في نهايات الكلمات.

    مقياس **تكميلي** يُبلَّغ بجانب WER الصارم لا بدلًا منه. سببه أن جزءًا
    من "أخطاء الكلمات" في العربية ليس خطأ تعرّف على الكلام بل تباين
    إملائي مشروع: «اليوغا/اليوغه»، «بوذا/بوذه». هذه تُسمع متطابقة تقريبًا
    وتُكتب بصيغتين، ولا تؤثر على فهم القارئ للمستند.

    لا يُستخدم لتجميل الرقم: يُنشر الاثنان معًا دائمًا.
    """
    text = normalize_for_scoring(text)
    # توحيد نهايات الكلمات المتشابهة صوتيًا
    text = re.sub(r"[اهي](?=\s|$)", "ا", text)
    return text


def wer_tolerant(reference: str, hypothesis: str) -> tuple[float, int, int]:
    """WER مع تسامح إملائي وتجاهل حدود الكلمات المُدرَجة خطأً."""
    ref = normalize_orthography_tolerant(reference).split()
    hyp = normalize_orthography_tolerant(hypothesis).split()
    if not ref:
        return (0.0 if not hyp else 1.0), 0, 0
    distance = edit_distance(ref, hyp)
    return distance / len(ref), distance, len(ref)


def edit_distance(a: List[str], b: List[str]) -> int:
    """مسافة ليفنشتاين بذاكرة O(min(n,m))."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, token_a in enumerate(a, start=1):
        current = [i]
        for j, token_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1,        # حذف
                               current[j - 1] + 1,     # إدراج
                               previous[j - 1] + (token_a != token_b)))
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> tuple[float, int, int]:
    ref = normalize_for_scoring(reference).split()
    hyp = normalize_for_scoring(hypothesis).split()
    if not ref:
        return (0.0 if not hyp else 1.0), 0, 0
    distance = edit_distance(ref, hyp)
    return distance / len(ref), distance, len(ref)


def cer(reference: str, hypothesis: str) -> tuple[float, int, int]:
    ref = list(normalize_for_scoring(reference).replace(" ", ""))
    hyp = list(normalize_for_scoring(hypothesis).replace(" ", ""))
    if not ref:
        return (0.0 if not hyp else 1.0), 0, 0
    distance = edit_distance(ref, hyp)
    return distance / len(ref), distance, len(ref)


def main() -> int:
    parser = argparse.ArgumentParser(description="قياس دقة التفريغ العربي")
    parser.add_argument("--eval-set", required=True, type=Path)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output", type=Path, default=Path("wer_report.json"))
    args = parser.parse_args()

    from audio.transcriber import TranscriptionEngine
    from config.settings import TranscriptionConfig

    samples = json.loads(args.eval_set.read_text(encoding="utf-8"))
    config = TranscriptionConfig(model_size=args.model, device=args.device)
    engine = TranscriptionEngine(config)
    # يُحمَّل النموذج مرة واحدة لكل الدفعة بدل مرة لكل مقطع.
    # (الإنتاج يحرّر النموذج بعد كل مهمة عمدًا؛ هنا نقيس الدقة لا الذاكرة.)
    engine.keep_model_loaded = True

    results, total_wer_err, total_wer_len = [], 0, 0
    total_cer_err, total_cer_len, total_audio, total_time = 0, 0, 0.0, 0.0
    total_tol_err, total_tol_len = 0, 0

    print(f"النموذج: {args.model} | عدد المقاطع: {len(samples)}\n")
    for index, sample in enumerate(samples, start=1):
        start = time.time()
        transcript = engine.transcribe(Path(sample["file"]))
        elapsed = time.time() - start
        hypothesis = transcript.full_text_clean or transcript.full_text_raw
        reference = sample.get("norm") or sample.get("raw", "")

        w, we, wl = wer(reference, hypothesis)
        c, ce, cl = cer(reference, hypothesis)
        t, te, tl = wer_tolerant(reference, hypothesis)
        total_wer_err += we; total_wer_len += wl
        total_cer_err += ce; total_cer_len += cl
        total_tol_err += te; total_tol_len += tl
        total_audio += sample.get("duration", 0.0); total_time += elapsed

        results.append({"name": sample.get("name", sample["file"]),
                        "reference": reference, "hypothesis": hypothesis,
                        "wer": round(w, 4), "cer": round(c, 4),
                        "wer_tolerant": round(t, 4),
                        "seconds": round(elapsed, 1)})
        print(f"[{index}/{len(samples)}] WER={w:6.2%}  CER={c:6.2%}  ({elapsed:.0f}s)")

    overall_wer = total_wer_err / total_wer_len if total_wer_len else 0.0
    overall_cer = total_cer_err / total_cer_len if total_cer_len else 0.0
    overall_tol = total_tol_err / total_tol_len if total_tol_len else 0.0
    report = {
        "model": args.model,
        "samples": len(samples),
        "audio_seconds": round(total_audio, 1),
        "processing_seconds": round(total_time, 1),
        "realtime_factor": round(total_time / total_audio, 2) if total_audio else None,
        "wer": round(overall_wer, 4),
        "cer": round(overall_cer, 4),
        "wer_orthography_tolerant": round(overall_tol, 4),
        "word_accuracy": round(1 - overall_wer, 4),
        "char_accuracy": round(1 - overall_cer, 4),
        "word_accuracy_tolerant": round(1 - overall_tol, 4),
        "details": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\n{'=' * 52}")
    print(f"WER الإجمالي     : {overall_wer:.2%}   (دقة الكلمات {1-overall_wer:.2%})")
    print(f"CER الإجمالي     : {overall_cer:.2%}   (دقة الحروف  {1-overall_cer:.2%})")
    print(f"WER متسامح إملائيًا: {overall_tol:.2%}   (دقة {1-overall_tol:.2%})")
    print(f"معامل الزمن الحقيقي: {report['realtime_factor']}×")
    print(f"التقرير: {args.output}")
    return 0


if __name__ == "__main__":
    from utils.console import enable_utf8_console
    enable_utf8_console()
    raise SystemExit(main())
