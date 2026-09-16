"""تصيير تقرير المقرّر — صفحة واحدة يفتحها رئيس القسم لا المعلّم.

الجمهور مختلف عن بقية مخرجات البرنامج، والتصميم يتبعه: لا نصّ محاضرة
ولا صور، بل جدولٌ يُقرأ بالمسح السريع وأرقامٌ تُقارَن. والسطر الأهمّ في
الصفحة كلّها هو **تقرير التغطية**: ما ينقص، وكم محاضرة ينقصها، والأمر
الذي يزيله.

والروابط إلى مخرجات كل محاضرة نسبيّة من موضع التقرير: صفحةٌ تُحفظ في
جذر مجلد الإخراج تفتح مستندات المهامّ كلّها بنقرة. ونقلُها خارجه يكسر
الروابط وحدها ويُبقي الأرقام — وهو التنازل الصحيح: البديل تضمينُ كل شيء
في ملفّ واحد يزن مئات الميغابايت.
"""
from __future__ import annotations

import html
import os
from pathlib import Path
from typing import List, Optional

from core.course import EXPECTED_OUTPUTS, CourseReport, LectureRow
from utils.timestamps import seconds_to_display

_CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#15161c;--muted:#5b6070;--line:#e3e5ee;
  --brand:#2D2E82;--soft:#ececf7;--ok:#0f7b4f;--ok-soft:#e6f5ee;
  --warn:#8a6100;--warn-soft:#fdf3dd;--bad:#b3261e;--bad-soft:#fbe9e7}
@media (prefers-color-scheme:dark){:root{--bg:#14151c;--card:#1c1e28;
  --ink:#eceef6;--muted:#9aa0b4;--line:#2b2e3c;--brand:#8f90e8;--soft:#23253a;
  --ok:#5ed6a4;--ok-soft:#18342a;--warn:#e8c065;--warn-soft:#332a13;
  --bad:#ff8a80;--bad-soft:#3a1d1a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);direction:rtl;font-size:15px;
  line-height:1.8;font-family:"Segoe UI","Tahoma","Noto Naskh Arabic","Arial",sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:0 18px 70px}
header.m{background:var(--brand);color:#fff;padding:24px 18px;margin-bottom:22px}
header.m .in{max-width:1160px;margin:0 auto}
header.m h1{margin:0 0 4px;font-size:1.5rem}
header.m .sub{opacity:.85;font-size:.88rem;direction:ltr;unicode-bidi:isolate;text-align:right}
h2{font-size:1.12rem;color:var(--brand);border-bottom:2px solid var(--soft);
  padding-bottom:7px;margin:30px 0 13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px}
.tile .n{font-size:1.7rem;font-weight:700;color:var(--brand);
  font-variant-numeric:tabular-nums;direction:ltr;unicode-bidi:isolate}
.tile .l{font-size:.82rem;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:4px 0;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:right;padding:9px 12px;border-bottom:1px solid var(--line);
  vertical-align:top;white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:.79rem}
td.name{white-space:normal;min-width:190px}
tr:last-child td{border-bottom:0}
.num{font-variant-numeric:tabular-nums;direction:ltr;unicode-bidi:isolate;
  display:inline-block}
.pill{font-size:.74rem;border-radius:999px;padding:2px 9px;white-space:nowrap}
.ok{background:var(--ok-soft);color:var(--ok);border:1px solid var(--ok)}
.warn{background:var(--warn-soft);color:var(--warn);border:1px solid var(--warn)}
.bad{background:var(--bad-soft);color:var(--bad);border:1px solid var(--bad)}
.out a{display:inline-block;font-size:.74rem;border-radius:6px;padding:1px 7px;
  margin:1px 0 1px 3px;background:var(--soft);color:var(--brand);text-decoration:none}
.out a:hover{outline:1px solid var(--brand)}
.out span.gone{display:inline-block;font-size:.74rem;padding:1px 7px;margin:1px 0 1px 3px;
  color:var(--muted);border:1px dashed var(--line);border-radius:6px}
ul.rem{margin:0;padding:0;list-style:none}
ul.rem li{display:flex;justify-content:space-between;gap:14px;align-items:center;
  border-bottom:1px solid var(--line);padding:9px 2px}
ul.rem li:last-child{border-bottom:0}
code{background:var(--soft);color:var(--brand);border-radius:6px;padding:2px 8px;
  font-size:.83rem;direction:ltr;unicode-bidi:isolate;display:inline-block}
.err{color:var(--bad);font-size:.8rem;white-space:normal}
.empty{color:var(--muted);padding:18px;text-align:center}
@media print{body{background:#fff}.card{border-color:#ccc}
  header.m{background:#fff;color:#000;border-bottom:2px solid #000}}
"""


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def _rel(target: Path, base: Path) -> str:
    """مسار نسبيّ للرابط. يسقط إلى الاسم وحده عبر الأقراص على ويندوز."""
    try:
        return os.path.relpath(target, base).replace(os.sep, "/")
    except ValueError:
        return target.name


def _quality_pill(row: LectureRow) -> str:
    if row.quality_score is None:
        return '<span class="pill">—</span>'
    css = "ok" if row.quality_score >= 70 else (
        "warn" if row.quality_score >= 45 else "bad")
    title = " · ".join(row.quality_warnings[:3])
    return (f'<span class="pill {css}" title="{_esc(title)}">'
            f'<span class="num">{row.quality_score:.0f}%</span></span>')


def _status_pill(row: LectureRow) -> str:
    css = "ok" if row.is_complete else (
        "bad" if row.status == "failed" else "warn")
    label = _esc(row.status_label)
    if not row.is_complete and row.progress:
        label += f' <span class="num">{row.progress:.0f}%</span>'
    return f'<span class="pill {css}">{label}</span>'


def _outputs_cell(row: LectureRow, base: Path) -> str:
    chips: List[str] = []
    for suffix in EXPECTED_OUTPUTS:
        label = EXPECTED_OUTPUTS[suffix][0]
        if suffix not in row.outputs:
            continue
        match = next((p for p in sorted(row.job_dir.iterdir())
                      if p.is_file() and p.name.endswith(suffix)), None)
        if match is not None:
            chips.append(f'<a href="{_esc(_rel(match, base))}">{_esc(label)}</a>')
    # الناقص يظهر باهتًا لا يختفي: الفراغ يُقرأ «لم يُطلب»، والباهت
    # يُقرأ «مطلوب ولم يخرج» — وهو الفرق الذي يبني عليه القارئ قراره.
    for suffix in row.missing:
        chips.append(f'<span class="gone">{_esc(EXPECTED_OUTPUTS[suffix][0])}</span>')
    return "".join(chips) or "—"


def _tiles(report: CourseReport) -> str:
    quality = report.average_quality
    tiles = [
        (f"{report.completed} / {report.total}", "محاضرة مكتملة"),
        (seconds_to_display(report.total_seconds), "إجمالي المدّة"),
        (str(sum(row.sections for row in report.lectures)), "قسمًا"),
        (str(sum(row.figures for row in report.lectures)), "شكلًا"),
    ]
    if report.total_questions:
        tiles.append((str(report.total_questions), "سؤال تقييم"))
    if quality is not None:
        tiles.append((f"{quality:.0f}%", "متوسّط قابلية القراءة"))
    return ('<div class="tiles">' + "".join(
        f'<div class="tile"><div class="n">{_esc(value)}</div>'
        f'<div class="l">{_esc(label)}</div></div>'
        for value, label in tiles) + "</div>")


def _table(report: CourseReport, base: Path) -> str:
    if not report.lectures:
        return ('<div class="card"><div class="empty">'
                'لا مهامّ في هذا المجلد بعد.</div></div>')
    rows: List[str] = []
    for index, row in enumerate(report.lectures, start=1):
        error = (f'<div class="err">{_esc(row.last_error[:160])}</div>'
                 if row.last_error else "")
        rows.append(
            f'<tr><td class="num">{index}</td>'
            f'<td class="name">{_esc(row.name)}{error}</td>'
            f"<td>{_status_pill(row)}</td>"
            f'<td class="num">{_esc(seconds_to_display(row.duration_seconds))}</td>'
            f'<td class="num">{row.sections}</td>'
            f'<td class="num">{row.figures}</td>'
            f'<td class="num">{row.questions or "—"}</td>'
            f"<td>{_quality_pill(row)}</td>"
            f'<td class="out">{_outputs_cell(row, base)}</td></tr>')
    return ('<div class="card"><table><tr><th>#</th><th>المحاضرة</th>'
            "<th>الحالة</th><th>المدّة</th><th>أقسام</th><th>أشكال</th>"
            "<th>أسئلة</th><th>القراءة</th><th>المخرجات</th></tr>"
            + "".join(rows) + "</table></div>")


def _coverage(report: CourseReport) -> str:
    if not report.remedies:
        return ('<div class="card"><div class="empty">'
                'كل المحاضرات مكتملة وبكل مخرجاتها. ✓</div></div>')
    items = "".join(
        f'<li><code>{_esc(remedy)}</code>'
        f'<span class="num">{count} محاضرة</span></li>'
        for remedy, count in sorted(report.remedies.items(),
                                    key=lambda item: -item[1]))
    return f'<div class="card"><ul class="rem">{items}</ul></div>'


def render_html(report: CourseReport, base: Optional[Path] = None) -> str:
    base = Path(base or report.output_dir)
    title = _esc(report.title or "تقرير المقرّر")
    return (
        "<!doctype html>\n"
        '<html lang="ar" dir="rtl">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>تقرير المقرّر — {title}</title>\n<style>{_CSS}</style>\n"
        f'</head>\n<body>\n<header class="m"><div class="in">'
        f"<h1>تقرير المقرّر — {title}</h1>"
        f'<div class="sub">{_esc(report.output_dir)}</div>'
        "</div></header>\n<div class=\"wrap\">"
        f"{_tiles(report)}"
        "<h2>المحاضرات</h2>"
        f"{_table(report, base)}"
        "<h2>تقرير التغطية — ما ينقص وكيف يُستدرَك</h2>"
        f"{_coverage(report)}"
        "</div>\n</body>\n</html>\n")
