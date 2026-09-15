"""انحدار على أسوأ عطل ممكن: الحزمة المغلَّفة لا تفتح، وبلا أي رسالة.

القصة: ``build.spec`` يشحن ffmpeg وffprobe بوجهة ``"."`` — أي في جذر
``_internal`` داخل حزمة PyInstaller 6. و``utils/media_probe.py`` يعرف
ذلك ويقرأ ``sys._MEIPASS``، بينما ``utils/deps.py`` كان يبني المسار من
موقع ملفه:

    Path(__file__).resolve().parents[1] / "bin"      →  _internal/bin

مجلد لا وجود له. فيفشل فحص الثنائيات على **كل** جهاز لا يملك ffmpeg في
``PATH`` — أي على جهاز المستخدم النهائي بالضبط — ويُرفَع ``SystemExit``
قبل أن توجد نافذة Qt، والحزمة تُبنى بـ ``console=False``. النتيجة:
المعلّم ينقر مرّتين ولا يحدث شيء إطلاقًا.

ولم يكن لـ CI أن يمسكه: خطوة البناء تتحقّق من **وجود** الملف التنفيذي
ولا تشغّله.

الاختبارات هنا تُحاكي الحزمة المجمّدة بضبط ``sys.frozen`` و
``sys._MEIPASS`` — وهي الطريقة الوحيدة لاختبار هذا المسار على لينكس.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from utils import bundle, deps


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """يجعل الوحدات تظنّ أنها داخل حزمة PyInstaller جذرها ``tmp_path``."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    yield tmp_path


def test_bundled_dir_follows_meipass_when_frozen(frozen):
    assert bundle.is_frozen() is True
    assert bundle.bundled_dir() == frozen


def test_bundled_dir_is_the_source_bin_when_not_frozen():
    assert bundle.is_frozen() is False
    assert bundle.bundled_dir() == bundle.SOURCE_ROOT / "bin"


def test_deps_and_media_probe_agree_on_where_binaries_live(frozen):
    """المصدر الواحد — وهو جوهر الإصلاح.

    كان الملفان يجيبان إجابتين مختلفتين عن السؤال نفسه، فمرّ الفحص على
    حزمة لا تعمل. لو عاد أحدهما يبني المسار بنفسه سقط هذا الاختبار.
    """
    from utils import media_probe

    assert media_probe._bundled_dir() == bundle.bundled_dir() == frozen


def test_binary_check_finds_the_bundled_executable(frozen, monkeypatch):
    """الثنائي المشحون يُعثَر عليه حتى مع ``PATH`` فارغ تمامًا.

    هذا هو السيناريو الحقيقي: جهاز معلّم بلا ffmpeg مثبَّت.
    """
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    for name in ("ffmpeg", "ffprobe"):
        (frozen / bundle.binary_name(name)).write_bytes(b"\x7fELF")

    results = {r.name: r for r in deps.check_binaries()}
    assert results["ffmpeg"].ok, results["ffmpeg"].detail
    assert results["ffprobe"].ok, results["ffprobe"].detail
    assert str(frozen) in results["ffmpeg"].detail


def test_binary_check_fails_loudly_when_the_bundle_is_incomplete(frozen, monkeypatch):
    """حزمة ناقصة تفشل — لكن بنصيحة يفهمها المستخدم لا بأمر pip."""
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)

    results = {r.name: r for r in deps.check_binaries()}
    assert not results["ffmpeg"].ok
    assert "المثبِّت" in results["ffmpeg"].fix
    assert "pip" not in results["ffmpeg"].fix
    assert "setup_ffmpeg" not in results["ffmpeg"].fix


def test_no_pip_commands_are_offered_inside_a_frozen_build(frozen):
    """‏``sys.executable`` في الحزمة هو ``VideoToArabicWord.exe``.

    فأمر ``"VideoToArabicWord.exe" -m pip install PyQt6`` سطر بلا معنى
    يُملى على معلّم غير تقني — والحزمة تحمل تبعياتها معها أصلًا، فأي
    نقص فيها عطبُ بناء لا يصلحه المستخدم.
    """
    assert deps._pip_command() == ""

    diagnosis = deps.Diagnosis(results=[
        deps.CheckResult("PyQt6", False, "مفقودة", deps._reinstall_hint()),
    ])
    # النصيحة عربية، فلا تتسرّب إلى كتلة الأوامر القابلة للنسخ
    assert deps.fix_commands(diagnosis) == []
    assert "أعد تنزيل المثبِّت" in deps.format_report(diagnosis)


def test_source_checkout_still_offers_a_real_pip_command():
    """التشغيل من المستودع لم يتغيّر: المطوّر يحتاج الأمر الفعلي."""
    assert deps._pip_command().endswith("-m pip")
    assert sys.executable in deps._pip_command()


def test_app_reports_startup_failure_instead_of_exiting_silently(monkeypatch):
    """‏``app.main`` لا يرفع ``SystemExit`` عند فشل الفحص.

    الحزمة بلا طرفية: ``SystemExit`` برسالة نصّية تذهب إلى العدم.
    الآن يمرّ التقرير عبر ``_report_startup_failure`` الذي يعرض نافذة.
    """
    app = importlib.import_module("app")

    failing = deps.Diagnosis(results=[
        deps.CheckResult("ffmpeg", False, "غير موجود", "افعل كذا"),
    ])
    monkeypatch.setattr(app, "diagnose", lambda: failing)

    shown: list[str] = []
    monkeypatch.setattr(app, "_show_failure_dialog",
                        lambda report, commands: shown.append(report) or True)

    assert app.main() == 1
    assert shown, "لم تُعرض أي نافذة — عاد السلوك إلى الخروج الصامت"
    assert "ffmpeg" in shown[0]


def test_startup_failure_falls_back_when_no_gui_is_available(monkeypatch, capsys):
    """‏PyQt6 نفسه مفقود: لا نافذة ممكنة — لكن لا انهيار أيضًا."""
    app = importlib.import_module("app")
    monkeypatch.setattr(app, "_show_failure_dialog", lambda *_: False)
    monkeypatch.setattr(app, "_show_failure_message_box_win32", lambda *_: False)

    assert app._report_startup_failure("تقرير الفشل", []) == 1
    assert "تقرير الفشل" in capsys.readouterr().out


def test_selftest_mode_reports_the_environment_and_exits(monkeypatch, capsys):
    """‏``--selftest`` هو ما يجعل CI قادرًا على رؤية عطل تغليف.

    خطوة البناء كانت تتحقّق من **وجود** الملف التنفيذي فقط، والوجود لا
    يعني الإقلاع: حزمة لا تفتح إطلاقًا كانت تمرّ منها. هذا الوضع يشغّل
    الفحص بمسارات الحزمة الحقيقية ويُعيد رمز خروج.
    """
    app = importlib.import_module("app")
    monkeypatch.setattr(sys, "argv", ["app.py", "--selftest"])

    monkeypatch.setattr(app, "diagnose", lambda: deps.Diagnosis(results=[
        deps.CheckResult("ffmpeg", True, "/somewhere/ffmpeg")]))
    assert app.main() == 0

    monkeypatch.setattr(app, "diagnose", lambda: deps.Diagnosis(results=[
        deps.CheckResult("ffmpeg", False, "غير موجود")]))
    assert app.main() == 1, "الفحص الذاتي مرّ رغم بيئة معطوبة"

    assert "APP_VERSION=" in capsys.readouterr().out


# ── فحص الحزمة: tools/build_smoke.py ─────────────────────────────────

def test_build_smoke_understands_the_pyinstaller_6_layout(tmp_path):
    """‏PyInstaller 6 يضع كل شيء تحت ``_internal`` عدا التنفيذي.

    كان الفحص يفترض التسطيح (نمط 5)، فيبلّغ عن ثنائيات مفقودة في حزمة
    سليمة تمامًا — إنذار كاذب يُدرَّب الناس على تجاهله.
    """
    from tools import build_smoke

    flat = tmp_path / "flat"
    flat.mkdir()
    assert build_smoke.payload_dir(flat) == flat

    modern = tmp_path / "modern"
    (modern / "_internal").mkdir(parents=True)
    assert build_smoke.payload_dir(modern) == modern / "_internal"


def _make_bundle(root: Path, *, complete: bool = True) -> Path:
    payload = root / "_internal"
    (payload / "assets").mkdir(parents=True)
    (root / build_smoke_exe_name()).write_bytes(b"MZ")
    (payload / "assets" / "logo.png").write_bytes(b"\x89PNG")
    if complete:
        for name in ("ffmpeg", "ffprobe"):
            (payload / bundle.binary_name(name)).write_bytes(b"\x7fELF")
        # ملفّات بيانات حزم الطرف الثالث جزءٌ من «الحزمة الكاملة» —
        # غيابُ نموذج VAD وحده كان يُسقط كل تفريغ في حزمة تبدو سليمة.
        from tools import build_smoke

        for relative in build_smoke.BUNDLED_DATA_FILES:
            target = payload / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"onnx")
    return root


def build_smoke_exe_name() -> str:
    from tools import build_smoke

    return build_smoke._exe_name()


def test_build_smoke_accepts_a_complete_bundle(tmp_path):
    from tools import build_smoke

    assert build_smoke.validate_layout(_make_bundle(tmp_path / "ok")) == []


def test_build_smoke_rejects_a_bundle_missing_its_binaries(tmp_path):
    """بلا ffmpeg مشحون يعتمد التطبيق على ``PATH`` — وهو غائب عند المعلّم."""
    from tools import build_smoke

    errors = build_smoke.validate_layout(
        _make_bundle(tmp_path / "bad", complete=False))
    assert any("ffmpeg" in e for e in errors)
    assert any("ffprobe" in e for e in errors)


def test_build_smoke_fails_when_the_bundle_cannot_start(tmp_path, monkeypatch):
    """الفحص الحقيقي: الحزمة تُشغَّل، ورمز خروج غير صفري يُبلَّغ عنه."""
    from tools import build_smoke

    root = _make_bundle(tmp_path / "dead")

    class Result:
        returncode = 1
        stdout = "1 مشكلة / 1 problem(s) blocking startup."
        stderr = ""

    monkeypatch.setattr(build_smoke.subprocess, "run",
                        lambda *_a, **_k: Result())
    errors = build_smoke.run_selftest(root)
    assert errors and "لا تُقلع" in errors[0]


def test_bin_path_is_not_rebuilt_by_hand_anywhere():
    """حارس: لا يعود أحد يبني مسار ``bin`` من موقع ملفه.

    هذا هو الشكل الذي سبّب العطل، وهو سهل التكرار عند إضافة فحص جديد.
    """
    offenders = []
    for path in (bundle.SOURCE_ROOT / "utils").glob("*.py"):
        if path.name == "bundle.py":
            continue
        source = path.read_text(encoding="utf-8")
        if 'parents[1] / "bin"' in source or "parents[1] / 'bin'" in source:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders}: استعمل utils.bundle.bundled_dir() بدل بناء المسار يدويًا")
