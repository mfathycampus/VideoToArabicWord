"""مقارنة محرّكات التفريغ على مادتك أنت.

    python tools/benchmark_asr.py --eval-set my_eval.json
    python tools/benchmark_asr.py --eval-set my_eval.json --engines faster-whisper cohere-arabic

لماذا هذه الأداة بدل الاعتماد على أرقام منشورة:
    كل جهة تنشر WER على مجموعتها هي. Cohere تنشر 25.87 مقابل 36.86
    لـ Whisper على لهجات صعبة؛ وقياسنا على FLEURS الفصيحة أعطى 7.46
    لـ turbo. الرقمان صحيحان ولا يقارنان — اختلاف المادة يفوق اختلاف
    النماذج. القرار الصحيح يُتَّخذ على تسجيلاتك وجهازك.

صيغة ملف التقييم: قائمة من
    {"file": "مسار wav", "norm": "النص المرجعي الصحيح"}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio.engines.registry import ENGINE_ORDER, build_engine  # noqa: E402
from tools.measure_wer import cer, wer, wer_tolerant  # noqa: E402
from utils.timestamps import humanize_duration  # noqa: E402


def evaluate(engine_name: str, samples: list[dict], device: str) -> dict:
    engine = build_engine(engine_name)
    if not engine.is_available():
        return {"engine": engine_name, "available": False,
                "install_hint": engine.info.install_hint,
                "label": engine.info.label_ar}

    if hasattr(engine, "keep_model_loaded"):
        engine.keep_model_loaded = True

    totals = {"we": 0, "wl": 0, "ce": 0, "cl": 0, "te": 0, "tl": 0}
    audio_seconds = 0.0
    started = time.monotonic()
    failures = 0

    for index, sample in enumerate(samples, start=1):
        try:
            result = engine.transcribe(Path(sample["file"]))
        except Exception as exc:
            failures += 1
            print(f"    [{index}] فشل: {str(exc)[:80]}")
            continue
        hypothesis = result.full_text_clean or result.full_text_raw
        reference = sample.get("norm") or sample.get("raw", "")

        _, we, wl = wer(reference, hypothesis)
        _, ce, cl = cer(reference, hypothesis)
        _, te, tl = wer_tolerant(reference, hypothesis)
        totals["we"] += we; totals["wl"] += wl
        totals["ce"] += ce; totals["cl"] += cl
        totals["te"] += te; totals["tl"] += tl
        audio_seconds += sample.get("duration", 0.0)
        print(f"    [{index}/{len(samples)}] تم")

    elapsed = time.monotonic() - started
    engine.release()

    if not totals["wl"]:
        return {"engine": engine_name, "available": True, "failed": True,
                "label": engine.info.label_ar}

    word_error = totals["we"] / totals["wl"]
    return {
        "engine": engine_name,
        "label": engine.info.label_ar,
        "available": True,
        "needs_torch": engine.info.needs_torch,
        "samples": len(samples) - failures,
        "audio_seconds": round(audio_seconds, 1),
        "processing_seconds": round(elapsed, 1),
        "realtime_factor": round(elapsed / audio_seconds, 2) if audio_seconds else None,
        "wer": round(word_error, 4),
        "cer": round(totals["ce"] / totals["cl"], 4),
        "wer_tolerant": round(totals["te"] / totals["tl"], 4),
        "word_accuracy": round(1 - word_error, 4),
        "char_accuracy": round(1 - totals["ce"] / totals["cl"], 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="مقارنة محرّكات التفريغ")
    parser.add_argument("--eval-set", required=True, type=Path)
    parser.add_argument("--engines", nargs="*", default=list(ENGINE_ORDER))
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output", type=Path, default=Path("asr_benchmark.json"))
    args = parser.parse_args()

    samples = json.loads(args.eval_set.read_text(encoding="utf-8"))
    audio = sum(s.get("duration", 0.0) for s in samples)
    print(f"مجموعة التقييم: {len(samples)} مقطعًا · "
          f"{humanize_duration(audio)} صوت\n")

    results = []
    for name in args.engines:
        print(f"── {name}")
        outcome = evaluate(name, samples, args.device)
        results.append(outcome)
        if not outcome.get("available"):
            print(f"    غير مثبّت. للتثبيت: {outcome.get('install_hint','—')}\n")
        elif outcome.get("failed"):
            print("    فشل على كل العيّنات.\n")
        else:
            print(f"    WER={outcome['wer']:.2%}  CER={outcome['cer']:.2%}  "
                  f"RTF={outcome['realtime_factor']}×\n")

    usable = [r for r in results if r.get("available") and not r.get("failed")]
    args.output.write_text(
        json.dumps({"eval_set": str(args.eval_set), "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if usable:
        print("=" * 74)
        print(f"{'المحرّك':<34}{'دقة كلمات':>11}{'دقة حروف':>11}{'الزمن':>9}")
        print("-" * 74)
        for row in sorted(usable, key=lambda r: r["wer"]):
            print(f"{row['label'][:33]:<34}"
                  f"{row['word_accuracy']:>10.2%} "
                  f"{row['char_accuracy']:>10.2%} "
                  f"{row['realtime_factor']:>7}×")
        print("=" * 74)
        best_accuracy = min(usable, key=lambda r: r["wer"])
        fastest = min(usable, key=lambda r: r["realtime_factor"] or 1e9)
        print(f"الأدق  : {best_accuracy['label']}")
        print(f"الأسرع : {fastest['label']}")
        if best_accuracy["engine"] != fastest["engine"]:
            print("لا يوجد فائز مطلق — اختر حسب أولويتك على هذه المادة.")
    print(f"\nالتقرير: {args.output}")
    return 0


if __name__ == "__main__":
    from utils.console import enable_utf8_console
    enable_utf8_console()
    raise SystemExit(main())
