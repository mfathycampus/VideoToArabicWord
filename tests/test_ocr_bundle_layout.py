"""1.21.2: OCR يعمل بحزمة Tesseract كما يشحنها المثبِّت — بلا ``tessdata/configs``.

على جهاز المستخدم خرجت 150 لقطة «غير مفحوصة» فحُجبت كلّها والمستند بلا صور:
الأمر طلب ملفّ الإعداد ``tsv`` من ``tessdata/configs``، والحزمة لا تحويه،
فخرج Tesseract ناجحًا بلا ``out.tsv``.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

import video.ocr as ocr


def _system_tessdata():
    exe = shutil.which("tesseract")
    if not exe:
        return None, None
    out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True)
    first = (out.stdout or out.stderr).splitlines()[0]
    if '"' not in first:
        return None, None
    return exe, Path(first.split('"')[1])


def test_reads_with_traineddata_only(tmp_path, monkeypatch):
    exe, tessdata = _system_tessdata()
    if not exe or not (tessdata / "eng.traineddata").exists():
        pytest.skip("Tesseract بحزمة الإنجليزية غير مثبَّت")
    from PIL import Image, ImageDraw, ImageFont

    bundle = tmp_path / "tessdata"
    bundle.mkdir()
    shutil.copy2(tessdata / "eng.traineddata", bundle)      # كما يشحنها المثبِّت
    assert not (bundle / "configs").exists()
    monkeypatch.setenv("TESSDATA_PREFIX", str(bundle))
    monkeypatch.setattr(ocr, "_cached_exe", exe)
    monkeypatch.setattr(ocr, "_cached_langs", "eng")

    image = tmp_path / "shot.png"
    canvas = Image.new("RGB", (900, 200), "white")
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:                                         # Pillow < 10.1
        font = ImageFont.load_default()
    ImageDraw.Draw(canvas).text((20, 60), "Password Login", fill="black", font=font)
    canvas.save(image)

    result = ocr.extract_text_and_words(image)
    assert not getattr(result, "failed", False)
    assert "Password" in " ".join(w["text"] for w in result[1])
