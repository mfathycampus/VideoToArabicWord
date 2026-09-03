"""يثبّت تبعيات محرّك تفريغ اختياري **في المفسّر الصحيح**.

    python tools/install_engine.py cohere

لماذا هذه الأداة موجودة أصلًا: أشهر عطل تثبيت في هذا المشروع ليس في
الحزم بل في **أي Python وصلت إليه**. كتابة ``pip install`` في PowerShell
قد تصيب مفسّرًا مختلفًا تمامًا عن الذي يشغّل التطبيق، فيرى المستخدم
«تم التثبيت بنجاح» في الطرفية و«حزم ناقصة» في البرنامج.

هذه الأداة تحلّ المشكلة بنيويًا: تُشغَّل بالمفسّر نفسه، وتثبّت في
``sys.executable`` حصرًا. إن شغّلتها بالمفسّر الذي يفتح ``app.py``،
فالحزم ستصل إليه بالتأكيد.

اختيار بناء PyTorch: يُكتشف كرت NVIDIA تلقائيًا. بلا كرت نُثبّت بناء
المعالج (~200 ميجابايت) بدل البناء الكامل (~2.5 جيجابايت) — فرق كبير
في التنزيل ولا فرق في النتيجة على جهاز بلا CUDA.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# بناء المعالج فقط — أصغر بعشر مرات من البناء الافتراضي على ويندوز
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

ENGINES = {
    "cohere": {
        "requirements": "requirements-cohere.txt",
        "engine_name": "cohere-arabic",
        "label": "Cohere Transcribe Arabic",
        # تُثبَّت على مرحلتين: torch من فهرسه الصحيح، ثم البقية
        "torch_packages": ["torch"],
        "packages": ["transformers>=5.4.0", "librosa>=0.10.0",
                     "soundfile>=0.12.0", "sentencepiece>=0.2.0",
                     "accelerate>=0.30.0"],
    },
}


def has_nvidia_gpu() -> bool:
    """وجود كرت NVIDIA — يحدّد أي بناء من PyTorch نُنزّل."""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True,
                                timeout=20)
        return result.returncode == 0
    except Exception:
        return False


def pip(*args: str) -> int:
    """يشغّل pip بالمفسّر الجاري — لا ``pip`` المجرّد."""
    command = [sys.executable, "-m", "pip", *args]
    print("\n> " + " ".join(command), flush=True)
    return subprocess.run(command).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="تثبيت تبعيات محرّك تفريغ اختياري في المفسّر الجاري")
    parser.add_argument("engine", nargs="?", default="cohere",
                        choices=sorted(ENGINES))
    parser.add_argument("--cpu-only", action="store_true",
                        help="أجبِر بناء المعالج من PyTorch حتى مع وجود كرت")
    parser.add_argument("--cuda", action="store_true",
                        help="أجبِر البناء الكامل بدعم CUDA")
    args = parser.parse_args()

    spec = ENGINES[args.engine]
    print("=" * 64)
    print(f"  تثبيت تبعيات: {spec['label']}")
    print("=" * 64)
    print(f"  المفسّر : {sys.executable}")
    print(f"  الإصدار : Python {sys.version.split()[0]}")

    use_cuda = args.cuda or (not args.cpu_only and has_nvidia_gpu())
    print(f"  كرت NVIDIA: {'موجود — بناء CUDA' if use_cuda else 'غير موجود — بناء المعالج'}")
    print("=" * 64)

    if pip("install", "--upgrade", "pip") != 0:
        print("\n⚠ تعذّرت ترقية pip — نواصل على أي حال.")

    # ---- 1) PyTorch من الفهرس المناسب ----
    torch_args = ["install", *spec["torch_packages"]]
    if not use_cuda:
        torch_args += ["--index-url", TORCH_CPU_INDEX]
    print("\n[1/2] تثبيت PyTorch — قد يستغرق عدة دقائق…")
    if pip(*torch_args) != 0:
        print("\n✗ فشل تثبيت PyTorch. الأسباب الشائعة:\n"
              "  • انقطاع الاتصال أو بطء التنزيل (أعد المحاولة)\n"
              "  • مساحة قرص غير كافية\n"
              "  • إصدار Python غير مدعوم من PyTorch", file=sys.stderr)
        return 1

    # ---- 2) بقية الحزم ----
    print("\n[2/2] تثبيت بقية التبعيات…")
    if pip("install", *spec["packages"]) != 0:
        print("\n✗ فشل تثبيت بقية الحزم.", file=sys.stderr)
        return 1

    # ---- 3) تحقق فعلي بنفس منطق البرنامج ----
    print("\n" + "=" * 64)
    print("  التحقق")
    print("=" * 64)
    try:
        from audio.engines.registry import build_engine

        ready, reason = build_engine(spec["engine_name"]).diagnose()
    except Exception as exc:
        print(f"✗ تعذّر الفحص: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for line in str(reason).splitlines():
        print(f"  {line}")
    if ready:
        print("\n✓ تم. افتح البرنامج واختر المحرّك من قائمة «المحرّك».")
        return 0
    print("\n✗ ما زال المحرّك غير جاهز — انظر السبب أعلاه.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
