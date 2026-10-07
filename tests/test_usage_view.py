"""عرض سجل الاستهلاك للعميل."""
import pytest

pytest.importorskip("PyQt6")


def test_usage_rows_show_balance_total_and_newest_first():
    from ui.rewrite_panel import format_usage_rows

    text = format_usage_rows({
        "credit_minutes": 41.5,
        "calls": [{"at": 1_760_000_000, "minutes": 1.25},
                  {"at": 1_760_000_600, "minutes": 2.5}]})
    lines = text.splitlines()
    assert "41.5" in lines[0]
    assert "عدد العمليات المسجّلة: 2" in lines[1] and "3.75" in lines[1]
    assert "2.50" in lines[3] and "1.25" in lines[4]      # الأحدث أولًا


def test_usage_rows_handle_empty_log():
    from ui.rewrite_panel import format_usage_rows

    assert "0" in format_usage_rows({}).splitlines()[1]
