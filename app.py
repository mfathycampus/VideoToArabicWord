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

from utils.console import enable_utf8_console  # noqa: E402
from utils.deps import require_ready  # noqa: E402


def main() -> int:
    enable_utf8_console()
    # يُفحص أولًا: استيراد ناقص ينهار برسالة غامضة بدل رسالة مفهومة
    require_ready()

    from PyQt6.QtWidgets import QApplication

    from config.settings import AppConfig, default_config_path
    from ui.main_window import MainWindow
    from utils.logger import setup_logger

    config_path = default_config_path()
    config = AppConfig.load(config_path)
    setup_logger(config.application.output_dir / "logs")

    app = QApplication(sys.argv)
    app.setApplicationName("محوّل الفيديو إلى Word")
    window = MainWindow(config, config_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
