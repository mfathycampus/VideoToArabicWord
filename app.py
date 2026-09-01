"""نقطة الدخول."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.deps import require_ready          # noqa: E402


def main() -> int:
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
