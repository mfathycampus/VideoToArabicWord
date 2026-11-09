"""1.21.2: لا نافذة طرفية سوداء على ويندوز لأي أداة يشغّلها البرنامج.

ظهرت نافذة ``tesseract.exe`` فوق البرنامج لكل لقطة أثناء «استخراج نص الشاشة»
لأن الاستدعاء نسي ``CREATE_NO_WINDOW``. هذا الفحص يمنع نسيانه في أي ملف.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("ai", "audio", "config", "core", "document", "licensing",
               "matching", "recording", "screenguide", "ui", "utils", "video")
#: أوامر لا تعمل إلا على macOS/لينكس — لا نافذة طرفية هناك.
NON_WINDOWS = {"open", "xdg-open", "/usr/bin/caffeinate", "/usr/sbin/ioreg"}


def _first_literal(call: ast.Call):
    if call.args and isinstance(call.args[0], (ast.List, ast.Tuple)):
        first = call.args[0].elts[0] if call.args[0].elts else None
        if isinstance(first, ast.Constant):
            return first.value
    return None


def test_every_subprocess_call_hides_its_console():
    offenders = []
    for folder in SOURCE_DIRS:
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.func.attr in {"run", "Popen", "call",
                                               "check_call", "check_output"}):
                    continue
                keywords = {k.arg for k in node.keywords}
                if "creationflags" in keywords or None in keywords:   # **kwargs
                    continue
                if _first_literal(node) in NON_WINDOWS:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "بلا CREATE_NO_WINDOW: " + "، ".join(offenders)
