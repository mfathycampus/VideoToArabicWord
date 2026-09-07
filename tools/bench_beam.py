"""يقيس ما يكلّفه إعدادُ تفريغٍ واحد من وقت، وما يغيّره في النصّ.

    python tools/bench_beam.py --audio a.wav --field beam_size --values 5 1
    python tools/bench_beam.py --audio a.wav --field word_timestamps \\
        --values true false

سبب وجود هذا الملف: التعليق في ``config/settings.py`` كان يقول إن
``beam_size=1`` «أسرع بمرّة ونصف إلى مرّتين… ساعة ونصف مقابل ثلاث
ساعات» — **ادّعاءٌ لم يُقَس** على مادّة عربية بنموذج الإنتاج. والتفريغ
‏85٪ من زمن التشغيل المقاس على جهاز المستخدم، فهذا أهمّ رقم في
المشروع كلّه.

ولا مرجع مصحَّح بيد هنا، فلا يجوز قول «أيّهما أدقّ». ما يجوز قوله شيئان
يُقاسان مباشرة:

* **كم يوفّر من وقت.**
* **كم يغيّر من النصّ** — بأخذ مخرج القيمة الأولى مرجعًا. لا يقول
  أيّهما أصحّ، لكنه يحدّ المخاطرة: انحراف 3٪ قرارٌ سهل، و15٪ ليس كذلك.

ما أخرجه على دقيقتين من شرح عربي حقيقي، ``large-v3-turbo/int8``:

    beam=5              وسيط 83.1 ث   RTF 0.692
    beam=1              وسيط 66.8 ث   RTF 0.557   ×1.24   انحراف 3.7٪
    word_timestamps=on  وسيط 79.1 ث
    word_timestamps=off وسيط 77.2 ث               ×1.02   ← لا مكسب
    خيط واحد            وسيط 125.1 ث              (مقابل 83.1 بخيطين)

**وثلاثة دروس منهجية مدفوعة الثمن هنا:**

١ · المحرّك يُحمَّل **قبل** التوقيت. أوّل قياس أدرج تحميل الأوزان داخل
المنطقة المُوقَّتة — أربع دقائق من قرص بارد — أي أضعاف الفرق المقيس.

٢ · التشتّت يُقاس ولا يُفترض. ``self_divergence`` يقارن تشغيلين بنفس
الإعداد: خرج **صفرًا** هنا، أي أن المخرج حتمي وأن كل فرق يُنسب إلى
الإعداد. وكنتُ قد قلتُ من قبل إن التفريغ غير حتمي بناءً على تشغيلين
اختلفا — الاختلاف كان في شيء آخر لم أضبطه.

٣ · النتيجة لا تُنقَل بين آلتين بلا شرط. هذه الأرقام من آلة تدعم
‏AVX-512 VNNI؛ وجهاز المستخدم (‏Ryzen 5 PRO 3400GE) يملك AVX2 وحده،
فيعمل عليه النموذج نفسه بنحو 2.3× من هذا الزمن. النِّسَب تُنقَل،
والأزمنة المطلقة لا.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import AppConfig  # noqa: E402
from tools.measure_wer import wer  # noqa: E402


def _slice(source: Path, seconds: float, out: Path) -> Path:
    """يقتطع مقطعًا ثابتًا. القياس يتكرّر على **نفس** الصوت بالضبط."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-t", str(seconds),
         "-ac", "1", "-ar", "16000", str(out)], check=True)
    return out


def _engine(threads: int):
    """محرّك واحد لكل القياسات.

    محرّك جديد لكل تشغيل يعيد تحميل الأوزان **داخل** المنطقة المُوقَّتة —
    وهو ما رأيتُه هنا: أربع دقائق من قرص بارد، أي أضعاف الفرق المقيس.
    ``keep_model_loaded`` يجعل هذا الحِمل يُدفع مرّة واحدة قبل القياس.
    """
    from audio.transcriber import TranscriptionEngine

    config = AppConfig().whisper
    config.cpu_threads = threads
    engine = TranscriptionEngine(config)
    engine.keep_model_loaded = True
    return engine


def _run_once(engine, field: str, value, audio: Path) -> tuple[float, str]:
    setattr(engine.config, field, value)
    started = time.monotonic()
    result = engine.transcribe(audio)
    elapsed = time.monotonic() - started
    return elapsed, (result.full_text_clean or result.full_text_raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threads", type=int, default=0)
    # حقلٌ واحد يُبدَّل بين قيمتين أو أكثر. عُمّم عن ``beam_size`` وحده
    # لأن السؤال الثاني — كم يكلّف ``word_timestamps`` — يحتاج نفس
    # الآلة بالضبط: نفس الصوت، نفس المحرّك المحمَّل، نفس عدد التكرارات.
    parser.add_argument("--field", default="beam_size")
    parser.add_argument("--values", nargs="+", default=["5", "1"])
    parser.add_argument("--out", type=Path, default=Path("bench_setting.json"))
    args = parser.parse_args()

    def _typed(text: str):
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        try:
            return int(text)
        except ValueError:
            return text

    values = [_typed(v) for v in args.values]

    clip = _slice(args.audio, args.seconds, Path("/tmp/bench_clip.wav"))
    engine = _engine(args.threads)

    # إحماء غير موقَّت: تحميل الأوزان وتسخين ذاكرة النظام. بدونه يحمل
    # أوّل قياس كلفةً لا علاقة لها بالإعداد المقيس.
    warm = _slice(args.audio, 8.0, Path("/tmp/bench_warm.wav"))
    print("إحماء…", flush=True)
    _run_once(engine, args.field, values[0], warm)
    print("جاهز — بدء القياس", flush=True)

    findings: dict = {}
    for value in values:
        times, texts = [], []
        for index in range(args.repeats):
            elapsed, text = _run_once(engine, args.field, value, clip)
            times.append(elapsed)
            texts.append(text)
            print(f"{args.field}={value} تشغيل {index + 1}: {elapsed:.1f}s "
                  f"(RTF {elapsed / args.seconds:.3f})", flush=True)
        findings[value] = {
            "times": times,
            "median": statistics.median(times),
            "spread": max(times) - min(times),
            "rtf": statistics.median(times) / args.seconds,
            "text": texts[0],
            "chars": len(texts[0]),
            # تشتّت النصّ بين تشغيلين بنفس الإعداد — سقفُ ما يمكن
            # نسبته إلى تغيير الإعداد. بلا هذا الرقم لا معنى للمقارنة.
            "self_divergence": (wer(texts[0], texts[1])[0]
                                if len(texts) > 1 else None),
        }

    baseline = values[0]
    for value in values[1:]:
        findings[value]["divergence_from_baseline"] = wer(
            findings[baseline]["text"], findings[value]["text"])[0]
        findings[value]["speedup"] = (findings[baseline]["median"]
                                      / findings[value]["median"])

    args.out.write_text(json.dumps(findings, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print("\n── الخلاصة ──")
    for value, data in findings.items():
        line = (f"{args.field}={value}  وسيط {data['median']:.1f}s  "
                f"تشتّت {data['spread']:.1f}s  RTF {data['rtf']:.3f}")
        if "speedup" in data:
            line += (f"  ×{data['speedup']:.2f} أسرع  "
                     f"انحراف النصّ {data['divergence_from_baseline'] * 100:.1f}٪")
        if data["self_divergence"] is not None:
            line += f"  (تشتّت ذاتي {data['self_divergence'] * 100:.1f}٪)"
        print(line)
    return 0


if __name__ == "__main__":
    # كل نقطة دخول تفعّل ترميز UTF-8 قبل أوّل طباعة: نافذة ويندوز
    # الافتراضية cp1252، وأوّل حرف عربي فيها يُسقط الأداة بـ
    # ``UnicodeEncodeError`` قبل أن يظهر أي رقم.
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
