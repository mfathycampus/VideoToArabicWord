"""نقطة الدخول."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── حارس OpenMP ─────────────────────────────────────────────────────
# يجب أن يسبق أي استيراد لمكتبة حسابية. ‏CTranslate2 (محرّك التفريغ)
# يوزّع عمله على أنوية المعالج عبر OpenMP. إن حُمّلت في العملية نفسها
# مكتبة أخرى تحمل بيئة OpenMP خاصة بها — PyTorch مثلًا — تنازعت
# البيئتان الأنويةَ وتضاعف زمن التفريغ.
#
# الحلّ الأساسي ألّا يُحمَّل PyTorch أصلًا (فحص المحرّكات صار
# ``find_spec`` بلا استيراد)، وهذا حزام أمان إضافي: نثبّت عدد خيوط
# OpenMP على أنوية المعالج ناقص واحدة إن لم يضبطها المستخدم.
if "OMP_NUM_THREADS" not in os.environ:
    _cores = os.cpu_count() or 4
    os.environ["OMP_NUM_THREADS"] = str(max(1, _cores - 1)
                                        if _cores > 2 else _cores)

from utils.bundle import bundled_dir, is_frozen  # noqa: E402
from utils.console import enable_utf8_console  # noqa: E402
from utils.deps import diagnose, fix_commands, format_report  # noqa: E402
from version import APP_VERSION  # noqa: E402

APP_TITLE = "محوّل الفيديو إلى Word"


def _show_failure_dialog(report: str, commands: list[str]) -> bool:
    """يعرض تشخيص البيئة في نافذة. يُعيد False إن تعذّر بناء النافذة.

    سبب وجود هذه الدالة: الحزمة تُبنى بـ ``console=False``، فلا طرفية
    تستقبل أي طباعة. كان الفشل يُرفَع كـ ``SystemExit`` برسالة نصّية
    تذهب إلى العدم — المستخدم ينقر مرّتين ولا يحدث **شيء**: لا نافذة
    ولا رسالة ولا مؤشّر انتظار. أسوأ سلوك ممكن لأول تشغيل.

    النافذة تُبنى قبل أي استيراد ثقيل، ولا تعتمد على شيء من التطبيق سوى
    PyQt6 — وإن كان هو نفسه المفقود سقطنا إلى مسار آخر في ``main``.
    """
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return False

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)

    box = QMessageBox()
    box.setWindowTitle(f"{APP_TITLE} — البيئة غير جاهزة")
    box.setIcon(QMessageBox.Icon.Critical)
    box.setText("التطبيق لا يستطيع الإقلاع.")
    box.setInformativeText(
        "الفحص التالي يوضّح السبب. اضغط «نسخ التقرير» وأرسله إن احتجت مساعدة."
        + ("\n\nأوامر الإصلاح:\n" + "\n".join(commands) if commands else ""))
    box.setDetailedText(report)
    copy_button = box.addButton("نسخ التقرير", QMessageBox.ButtonRole.ActionRole)
    box.addButton("إغلاق", QMessageBox.ButtonRole.RejectRole)
    box.exec()

    if box.clickedButton() is copy_button:
        clipboard = app.clipboard()
        if clipboard is not None:
            clipboard.setText(report)
            # نافذة ثانية قصيرة: بلا تأكيد لا يعرف المستخدم أن النسخ حدث.
            QMessageBox.information(None, APP_TITLE, "نُسخ التقرير إلى الحافظة.")
    return True


def _show_failure_message_box_win32(report: str) -> bool:
    """آخر ملاذ على ويندوز: نافذة النظام نفسها، بلا Qt ولا طرفية."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, report, f"{APP_TITLE} — البيئة غير جاهزة", 0x10)
        return True
    except Exception:
        return False


def _report_startup_failure(report: str, commands: list[str]) -> int:
    """يُوصل التشخيص بأفضل وسيلة متاحة، ويُعيد رمز الخروج."""
    print(report)                       # الطرفية حين توجد
    if _show_failure_dialog(report, commands):
        return 1
    if _show_failure_message_box_win32(report):
        return 1
    return 1


def _run_selftest() -> int:
    """يفحص البيئة ويخرج، بلا أي واجهة — للتحقّق الآلي من الحزمة.

    سبب وجوده: عطل الإقلاع الذي شلّ الحزمة المغلَّفة لم يكن لـ CI أن
    يراه، لأن خطوة البناء تتحقّق من **وجود** الملف التنفيذي ولا تشغّله
    إطلاقًا. والتشغيل الكامل مستحيل على عدّاء بلا واجهة رسومية.

    هذا الوضع يشغّل الحزمة الحقيقية — بمساراتها المجمّدة وثنائياتها
    المشحونة — ويُعيد رمز خروج. صار عندنا فحص يفشل يوم يقع العطل.

        VideoToArabicWord.exe --selftest
    """
    diagnosis = diagnose()
    print(format_report(diagnosis))
    print(f"\nAPP_VERSION={APP_VERSION}  frozen={is_frozen()}"
          f"  bundled_dir={bundled_dir()}")
    return 0 if diagnosis.ok else 1


def main() -> int:
    enable_utf8_console()

    argv = sys.argv[1:]
    if "--version" in argv:
        print(f"{APP_TITLE} {APP_VERSION}")
        return 0
    if "--selftest" in argv:
        return _run_selftest()

    # يُفحص أولًا: استيراد ناقص ينهار برسالة غامضة بدل رسالة مفهومة.
    diagnosis = diagnose()
    if not diagnosis.ok:
        return _report_startup_failure(format_report(diagnosis),
                                       fix_commands(diagnosis))

    from PyQt6.QtWidgets import QApplication

    from config.settings import AppConfig, default_config_path
    from ui.main_window import MainWindow
    from utils.logger import logger, setup_logger

    config_path = default_config_path()
    config = AppConfig.load(config_path)
    setup_logger(config.application.output_dir / "logs")
    # أول سطر في كل سجلّ: بلا هذا تبدأ كل محادثة دعم بسؤال «أي إصدار؟»
    logger.info("%s %s — %s", APP_TITLE, APP_VERSION,
                "حزمة مجمّدة" if is_frozen() else "تشغيل من المصدر")

    app = QApplication(sys.argv)
    # الثيم على التطبيق لا على النافذة: الحوارات نوافذ مستقلّة،
    # وتطبيقه على النافذة وحدها يترك حوارًا بلون النظام وسطها.
    from ui.theme import stylesheet
    app.setStyleSheet(stylesheet())
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    window = MainWindow(config, config_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
