"""انحدار على أخطر سبب بطء غير مرئي في هذا المشروع.

القصة: بعد تثبيت المحرّك الاختياري (‏Cohere، ويجرّ PyTorch) قفز زمن
معالجة **نفس الملف** من ~1:45 إلى أكثر من ثلاث ساعات — **دون أن يختار
المستخدم ذلك المحرّك**.

السبب: فحص التوفّر كان يستورد ``torch`` و ``transformers`` فعليًا لمجرد
كتابة «(غير مثبّت)» بجوار اسم في قائمة. و PyTorch يحمل بيئة OpenMP خاصة
به، بينما CTranslate2 (محرّك faster-whisper) يستعمل OpenMP أيضًا؛
بيئتان في عملية واحدة تتنازعان الأنوية فيتضاعف زمن التفريغ.

هذه الاختبارات تثبّت العلاج: الفحص يجيب على السؤال بلا تحميل شيء.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _in_clean_process(code: str) -> str:
    """يشغّل الشيفرة في عملية جديدة — الوحيدة التي يصحّ فيها قياس
    ``sys.modules``، لأن حزمة الاختبارات قد تكون استوردت شيئًا قبلها."""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT),
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip()


def test_probing_engines_never_imports_torch():
    output = _in_clean_process(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from audio.engines.registry import build_engine\n"
        "build_engine('cohere-arabic').diagnose()\n"
        "print('torch' in sys.modules, 'transformers' in sys.modules)\n")
    assert output == "False False", (
        f"الفحص حمّل مكتبات ثقيلة في العملية: {output}")


def test_probing_the_default_engine_stays_light():
    output = _in_clean_process(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from audio.engines.registry import build_engine\n"
        "build_engine('faster-whisper').is_available()\n"
        "print('torch' in sys.modules)\n")
    assert output == "False"


def test_listing_engines_for_the_ui_stays_light():
    """بناء الواجهة يستدعي هذه الدوال عند كل إقلاع."""
    output = _in_clean_process(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from audio.engines.registry import available_engines, listed_engines\n"
        "available_engines(include_optional=True)\n"
        "for n in listed_engines(True):\n"
        "    pass\n"
        "print('torch' in sys.modules, 'transformers' in sys.modules)\n")
    assert output == "False False"


def test_optional_engines_are_hidden_by_default():
    from audio.engines.registry import ENGINE_ORDER, listed_engines

    assert ENGINE_ORDER == ("faster-whisper",)
    assert listed_engines() == ("faster-whisper",)
    assert "cohere-arabic" in listed_engines(include_optional=True)


def test_optional_engine_is_still_reachable_by_name():
    """الإخفاء عن الواجهة لا يعني الحذف: من يريده يطلبه صراحةً."""
    from audio.engines.registry import build_engine

    engine = build_engine("cohere-arabic")
    assert engine.info.name == "cohere-arabic"


def test_diagnose_reports_a_reason_without_importing():
    from audio.engines.registry import build_engine

    ready, reason = build_engine("cohere-arabic").diagnose()
    assert isinstance(ready, bool)
    assert reason.strip()


def test_app_sets_an_openmp_thread_cap():
    """حزام الأمان: عدد خيوط OpenMP مضبوط قبل أي استيراد حسابي."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    guard = source.split("def main")[0]
    assert "OMP_NUM_THREADS" in guard, "الحارس يجب أن يسبق أي استيراد"
    # لا استيراد ثقيل قبل الحارس
    before = guard.split("OMP_NUM_THREADS")[0]
    for heavy in ("import torch", "import ctranslate2", "from faster_whisper",
                  "from PyQt6"):
        assert heavy not in before, f"{heavy} يسبق حارس OpenMP"


def test_missing_faster_whisper_gives_a_clear_arabic_message_not_a_raw_traceback():
    """faster-whisper (المحرّك الافتراضي الوحيد) غير مثبَّت: بناء الـ pipeline
    كان يُسرِّب ``ModuleNotFoundError`` خامًا حتى الواجهة. صار يتحوّل إلى
    ``ModelUnavailableError`` برسالة عربية واضحة وحل مقترح."""
    output = _in_clean_process(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "sys.modules['faster_whisper'] = None  # يجعل استيراده يفشل\n"
        "from pathlib import Path\n"
        "from config.settings import AppConfig\n"
        "from core.exceptions import ModelUnavailableError\n"
        "from core.pipeline import VideoToDocPipeline\n"
        "try:\n"
        "    VideoToDocPipeline(Path('/tmp/out'), AppConfig())\n"
        "    print('NO_ERROR_RAISED')\n"
        "except ModelUnavailableError as exc:\n"
        "    print('OK:' + str(exc))\n"
        "except Exception as exc:\n"
        "    print('WRONG_TYPE:' + type(exc).__name__ + ':' + str(exc))\n")
    assert output.startswith("OK:"), (
        f"لم تُرفَع رسالة عربية واضحة: {output}")
    assert "faster-whisper" in output
    assert "pip install" in output
