"""1.20.0: كل حزمة في المستودع تُشحن في الـwheel (كانت ``licensing`` تسقط)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_includes_every_source_package():
    import pytest
    try:
        import tomllib
    except ModuleNotFoundError:                       # Python 3.10
        tomllib = pytest.importorskip("tomli")
    from setuptools import find_packages

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    find = config["tool"]["setuptools"]["packages"]["find"]
    shipped = set(find_packages(str(ROOT), include=find["include"],
                                exclude=find.get("exclude", [])))
    on_disk = {
        ".".join(p.parent.relative_to(ROOT).parts)
        for p in ROOT.rglob("__init__.py")
        if p.parent.relative_to(ROOT).parts[0] not in {"tests", "build", "dist", ".git"}
    }
    assert on_disk <= shipped, f"حزم لا تُشحن: {sorted(on_disk - shipped)}"
    assert "licensing" in shipped and "document.exporters" in shipped
