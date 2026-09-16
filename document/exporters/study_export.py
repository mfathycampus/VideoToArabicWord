"""دليل المذاكرة وبطاقات المراجعة — تصيير ``StudyPack``.

صفحة واحدة مستقلّة كصفحة المستند تمامًا وبنفس القيود: بلا مرجع خارجي،
وتُطبع إلى PDF من المتصفّح، وكل توقيت فيها نقطة قفز في التسجيل.

**وما يميّزها عن أي ورقة أسئلة مولَّدة: المرجع بجوار كل سؤال.** المعلّم
يضغط التوقيت فيسمع الثانية التي بُني منها السؤال ويحكم بنفسه. وهذا ما
يجعل المراجعة ثوانيَ بدل إعادة سماع المحاضرة.

**والإجابات مخفيّة خلف زرّ لا محذوفة:** ورقةٌ بلا إجابات تحتاج ورقة
ثانية، وورقةٌ بإجابات ظاهرة لا تصلح للطالب. الكشف بضغطة يحلّ الاثنين
بملفّ واحد، وعند الطباعة تظهر الإجابات كلّها فتصير نسخة المعلّم.
"""
from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import List, Optional

from config.schemas import StudyPack
from document.exporters import ExportContext, register
from utils.logger import logger
from utils.timestamps import seconds_to_display

_KIND_LABEL = {"mcq": "اختيار من متعدد", "true_false": "صح أو خطأ",
               "short": "سؤال قصير"}
_LEVEL_LABEL = {"easy": "سهل", "medium": "متوسط", "hard": "متقدّم"}
_ORIGIN_LABEL = {"ai": "من النصّ", "screen": "من الشاشة",
                 "frequency": "متكرّر"}

_CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#15161c;--muted:#5b6070;--line:#e3e5ee;
  --brand:#2D2E82;--soft:#ececf7;--ok:#0f7b4f;--ok-soft:#e6f5ee}
@media (prefers-color-scheme:dark){:root{--bg:#14151c;--card:#1c1e28;
  --ink:#eceef6;--muted:#9aa0b4;--line:#2b2e3c;--brand:#8f90e8;--soft:#23253a;
  --ok:#5ed6a4;--ok-soft:#18342a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);direction:rtl;font-size:16px;
  line-height:1.9;font-family:"Segoe UI","Tahoma","Noto Naskh Arabic","Arial",sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:0 20px 80px}
header.masthead{background:var(--brand);color:#fff;padding:26px 20px;margin-bottom:22px}
header.masthead .inner{max-width:860px;margin:0 auto}
header.masthead h1{margin:0 0 4px;font-size:1.55rem}
header.masthead .sub{opacity:.85;font-size:.9rem}
h2{font-size:1.2rem;color:var(--brand);border-bottom:2px solid var(--soft);
  padding-bottom:8px;margin:32px 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin-bottom:14px}
ol.q{list-style:none;counter-reset:q;margin:0;padding:0}
ol.q > li{counter-increment:q}
.qhead{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}
.qnum{background:var(--brand);color:#fff;border-radius:50%;width:28px;height:28px;
  display:grid;place-items:center;font-size:.85rem;flex:none}
.qnum::before{content:counter(q)}
.tag{font-size:.75rem;color:var(--muted);background:var(--soft);
  border-radius:999px;padding:2px 10px}
.qtext{font-weight:600;margin:0 0 10px}
ul.opts{list-style:none;margin:0 0 10px;padding:0}
ul.opts li{border:1px solid var(--line);border-radius:8px;padding:8px 12px;
  margin-bottom:6px}
.answer{background:var(--ok-soft);border:1px solid var(--ok);border-radius:8px;
  padding:10px 12px;margin-top:8px;font-size:.93rem}
.answer b{color:var(--ok)}
.why{color:var(--muted);font-size:.88rem;margin-top:6px}
.ts{display:inline-block;direction:ltr;unicode-bidi:isolate;
  font-variant-numeric:tabular-nums;font-size:.78rem;padding:2px 9px;
  border-radius:999px;background:var(--soft);color:var(--brand);
  border:1px solid transparent;cursor:pointer;font-family:inherit}
.ts:hover{border-color:var(--brand)}
button.reveal{font:inherit;font-size:.85rem;background:var(--card);color:var(--brand);
  border:1px solid var(--brand);border-radius:8px;padding:5px 14px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:.93rem}
th,td{text-align:right;padding:9px 10px;border-bottom:1px solid var(--line);
  vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.82rem}
ul.obj{margin:0;padding-inline-start:22px}
ul.obj li{margin-bottom:7px}
details.dropped{margin-top:34px;font-size:.85rem;color:var(--muted)}
details.dropped summary{cursor:pointer}
details.dropped ul{padding-inline-start:22px}
#player{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:12px;margin-bottom:20px;position:sticky;top:0;z-index:5}
#player video{width:100%;border-radius:8px;display:block;background:#000}
#player .hint{font-size:.83rem;color:var(--muted);margin:0 0 8px}
.hidden{display:none!important}
@media print{
  #player,button.reveal,.ts{display:none!important}
  body{background:#fff;font-size:11.5pt}
  .answer{display:block!important}
  .card{break-inside:avoid;border-color:#ccc}
}
"""

_JS = """
(function(){
  var video=null;
  var pick=document.getElementById('pick');
  if(pick) pick.addEventListener('change',function(){
    var f=pick.files&&pick.files[0]; if(!f) return;
    var el=document.getElementById('vid');
    el.src=URL.createObjectURL(f); el.classList.remove('hidden');
    document.getElementById('pickWrap').classList.add('hidden'); video=el;
  });
  document.addEventListener('click',function(e){
    var r=e.target.closest('button.reveal');
    if(r){
      var box=document.getElementById(r.getAttribute('data-for'));
      var open=!box.classList.contains('hidden');
      box.classList.toggle('hidden');
      r.textContent = open ? 'إظهار الإجابة' : 'إخفاء الإجابة';
      return;
    }
    var b=e.target.closest('.ts'); if(!b) return;
    if(!video){ alert('اختر ملف التسجيل أولًا من أعلى الصفحة.'); return; }
    video.currentTime=parseFloat(b.getAttribute('data-t')||'0');
    video.play().catch(function(){});
    video.scrollIntoView({block:'nearest'});
  });
  var all=document.getElementById('revealAll');
  if(all) all.addEventListener('click',function(){
    var boxes=[].slice.call(document.querySelectorAll('.answer'));
    var show=boxes.some(function(b){return b.classList.contains('hidden');});
    boxes.forEach(function(b){ b.classList.toggle('hidden', !show); });
    [].forEach.call(document.querySelectorAll('button.reveal'),function(r){
      r.textContent = show ? 'إخفاء الإجابة' : 'إظهار الإجابة'; });
    all.textContent = show ? 'إخفاء كل الإجابات' : 'إظهار كل الإجابات';
  });
})();
"""


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _stamp(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    return (f'<button class="ts" data-t="{float(seconds):.3f}" '
            f'title="اسمع المقطع الذي بُني منه هذا">'
            f'{_esc(seconds_to_display(seconds))}</button>')


def _objectives(pack: StudyPack) -> str:
    if not pack.objectives:
        return ""
    items = "".join(
        f"<li>{_esc(o.text)} {_stamp(o.source_start)}</li>"
        for o in pack.objectives)
    return ('<h2>أهداف التعلّم</h2><div class="card">'
            f'<ul class="obj">{items}</ul></div>')


def _glossary(pack: StudyPack) -> str:
    if not pack.glossary:
        return ""
    rows = []
    for term in pack.glossary:
        definition = _esc(term.definition) or '<span style="opacity:.55">—</span>'
        rows.append(
            f"<tr><td><b>{_esc(term.term)}</b></td><td>{definition}</td>"
            f"<td>{_ORIGIN_LABEL.get(term.origin, term.origin)}"
            f" {_stamp(term.source_start)}</td></tr>")
    return ('<h2>مسرد المصطلحات</h2><div class="card"><table>'
            "<tr><th>المصطلح</th><th>التعريف</th><th>المصدر</th></tr>"
            + "".join(rows) + "</table></div>")


def _questions(pack: StudyPack) -> str:
    if not pack.questions:
        return ""
    items: List[str] = []
    for index, question in enumerate(pack.questions, start=1):
        box_id = f"a{index}"
        options = ""
        if question.kind == "mcq" and question.options:
            options = ('<ul class="opts">'
                       + "".join(f"<li>{_esc(o)}</li>" for o in question.options)
                       + "</ul>")
        why = (f'<div class="why">{_esc(question.explanation)}</div>'
               if question.explanation else "")
        items.append(
            '<li><div class="card">'
            '<div class="qhead"><span class="qnum"></span>'
            f'<span class="tag">{_KIND_LABEL.get(question.kind, question.kind)}</span>'
            f'<span class="tag">{_LEVEL_LABEL.get(question.difficulty, "")}</span>'
            f'{_stamp(question.source_start)}</div>'
            f'<p class="qtext">{_esc(question.question)}</p>{options}'
            f'<button class="reveal" data-for="{box_id}">إظهار الإجابة</button>'
            f'<div class="answer hidden" id="{box_id}">'
            f'<b>الإجابة:</b> {_esc(question.answer)}{why}</div>'
            "</div></li>")
    return ('<h2>أسئلة التقييم '
            '<button class="reveal" id="revealAll" style="float:left">'
            'إظهار كل الإجابات</button></h2>'
            f'<ol class="q">{"".join(items)}</ol>')


def _dropped(pack: StudyPack) -> str:
    if not pack.dropped:
        return ""
    # الشفافية هنا ليست زينة: المعلّم الذي يرى عشرة أسئلة ولا يعرف أن
    # ستّة رُفضت سيظنّ الأداة ضعيفة، والحقيقة أنها كانت دقيقة.
    items = "".join(f"<li>{_esc(reason)}</li>" for reason in pack.dropped[:60])
    return ('<details class="dropped"><summary>'
            f'أُسقط {len(pack.dropped)} عنصرًا في التحقّق — اضغط للتفاصيل'
            f'</summary><ul>{items}</ul></details>')


def render_study_html(pack: StudyPack, subtitle: str = "") -> str:
    title = _esc(pack.title or "دليل المذاكرة")
    return (
        "<!doctype html>\n"
        '<html lang="ar" dir="rtl">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>دليل المذاكرة — {title}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        '<header class="masthead"><div class="inner">'
        f"<h1>دليل المذاكرة — {title}</h1>"
        + (f'<div class="sub">{_esc(subtitle)}</div>' if subtitle else "")
        + "</div></header>\n<div class=\"wrap\">\n"
        '<div id="player"><p class="hint">اربط التسجيل لتصير التوقيتات '
        'بجوار كل سؤال نقاط قفز — الملف لا يُنسخ ولا يُرفع.</p>'
        '<div id="pickWrap"><input id="pick" type="file" accept="video/*,audio/*"></div>'
        '<video id="vid" class="hidden" controls preload="metadata"></video></div>\n'
        + _objectives(pack) + _questions(pack) + _glossary(pack) + _dropped(pack)
        + f"\n</div>\n<script>{_JS}</script>\n</body>\n</html>\n")


# ---------------------------------------------------------------------
def _load_pack(ctx: ExportContext) -> Optional[StudyPack]:
    """الحزمة من السياق، أو من ``study.json`` بجوار المستند.

    القراءة من القرص هي ما يجعل ``--rebuild`` يُعيد تصيير الدليل بلا
    استدعاء واحد للنموذج — وهو المسار الوحيد في البرنامج الذي قد يكلّف
    مالًا أو دقائق انتظار.
    """
    pack = ctx.options.get("study_pack")
    if isinstance(pack, StudyPack):
        return pack
    path = ctx.base_path.parent / "study.json"
    if not path.is_file():
        return None
    try:
        import json

        return StudyPack(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning(f"تعذّرت قراءة study.json: {exc}")
        return None


def export_study(ctx: ExportContext) -> Optional[Path]:
    pack = _load_pack(ctx)
    if pack is None:
        # الحزمة معطّلة أصلًا — الصمت هو الصحيح. سطرُ سجلٍّ هنا يظهر
        # لكل مستخدم في كل مهمة عن ميزة لم يطلبها.
        return None
    if pack.is_empty():
        logger.info("الحزمة التعليمية فارغة — يُتخطّى دليل المذاكرة.")
        return None
    path = ctx.sibling(".study.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_study_html(pack, ctx.plan.subtitle),
                    encoding="utf-8")
    return path


def export_flashcards(ctx: ExportContext) -> Optional[Path]:
    """بطاقات بصيغة يستوردها Anki مباشرةً.

    فاصلة لا جدولة، وترميز ``utf-8-sig``: بلا BOM يفتح Excel العربية
    محارفَ مشوّهة، وهو أوّل ما يجرّبه المعلّم قبل Anki.
    """
    pack = _load_pack(ctx)
    if pack is None or not pack.flashcards:
        return None
    path = ctx.sibling(".flashcards.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["الوجه", "الظهر", "التوقيت"])
        for card in pack.flashcards:
            writer.writerow([
                card.front, card.back,
                seconds_to_display(card.source_start)
                if card.source_start is not None else ""])
    return path


register("study", export_study)
register("flashcards", export_flashcards)
