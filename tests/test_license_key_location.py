"""1.20.0: المفتاح الخاص خارج مجلد المشروع، والقديم يُنقل بلا فقدان."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tool(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "make_license_under_test", ROOT / "tools" / "make_license.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LEGACY_KEY_FILE", tmp_path / "repo" / module.KEY_FILENAME)
    monkeypatch.setenv("VTAD_LICENSE_KEY", str(tmp_path / "user" / module.KEY_FILENAME))
    return module


def test_key_lives_outside_the_project_by_default(tool, monkeypatch):
    monkeypatch.delenv("VTAD_LICENSE_KEY")
    assert ROOT not in tool.key_path().parents


def test_move_key_moves_and_keeps_working(tool, capsys):
    tool.LEGACY_KEY_FILE.parent.mkdir()
    tool.LEGACY_KEY_FILE.write_text("SECRET\n", encoding="utf-8")
    assert tool._private_key() == "SECRET"                 # القديم يعمل مع تحذير
    assert "move-key" in capsys.readouterr().err
    assert tool.cmd_move_key(None) == 0
    assert not tool.LEGACY_KEY_FILE.exists()
    assert tool.key_path().read_text(encoding="utf-8").strip() == "SECRET"
    assert tool._private_key() == "SECRET"


def test_move_key_refuses_to_overwrite_a_different_key(tool):
    tool.LEGACY_KEY_FILE.parent.mkdir()
    tool.LEGACY_KEY_FILE.write_text("OLD", encoding="utf-8")
    tool.key_path().parent.mkdir(parents=True)
    tool.key_path().write_text("OTHER", encoding="utf-8")
    with pytest.raises(SystemExit):
        tool.cmd_move_key(None)
    assert tool.LEGACY_KEY_FILE.exists()                   # لم يُحذف شيء
