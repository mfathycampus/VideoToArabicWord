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
from pathlib import Path
from typing import List, Optional

from config.schemas import DocumentPlan, StudyPack
from document.exporters import ExportContext, register
from document.exporters import _design as D
from document.exporters._design import esc
from document.exporters._web import base_css
from utils.logger import logger
from utils.timestamps import seconds_to_display

_KIND_LABEL = {"mcq": "اختيار من متعدد", "true_false": "صح أو خطأ",
               "short": "سؤال قصير"}
_LEVEL_LABEL = {"easy": "سهل", "medium": "متوسط", "hard": "متقدّم"}
_ORIGIN_LABEL = {"ai": "من النصّ", "screen": "من الشاشة",
                 "frequency": "متكرّر"}
#: فوق هذا العدد تُطوى المصطلحات بلا تعريف تحت زرّ بدل أن تملأ الصفحة.
_CHIPS_VISIBLE = 18

_CSS = base_css() + """
.bar{position:sticky;top:0;z-index:20;background:var(--card);
  border-bottom:1px solid var(--line);box-shadow:0 2px 10px rgba(0,0,0,.04)}
.bar .in{display:flex;align-items:center;gap:6px;padding-top:8px;
  padding-bottom:8px;overflow-x:auto}
.bar a.nav{white-space:nowrap;text-decoration:none;color:var(--ink);
  padding:5px 13px;border-radius:999px;font-size:.88rem;font-weight:600}
.bar a.nav:hover,.bar a.nav.on{background:var(--soft);color:var(--brand)}
.bar .spacer{flex:1}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 90px}

/* أهداف التعلّم */
ul.obj{list-style:none;margin:0;padding:0;display:grid;gap:10px}
ul.obj li{display:flex;gap:12px;align-items:flex-start;background:var(--card);
  border:1px solid var(--line);border-radius:12px;padding:12px 16px}
ul.obj li::before{content:"✓";flex:none;width:26px;height:26px;border-radius:50%;
  background:var(--ok-soft);color:var(--ok);display:grid;place-items:center;
  font-weight:700;font-size:.85rem;margin-top:2px}

/* خريطة المراجعة */
.map{display:grid;gap:10px}
details.unit{background:var(--card);border:1px solid var(--line);
  border-radius:14px;overflow:hidden}
details.unit[open]{box-shadow:var(--shadow);border-color:var(--brand)}
details.unit summary{list-style:none;cursor:pointer;display:flex;
  align-items:center;gap:12px;padding:12px 16px}
details.unit summary::-webkit-details-marker{display:none}
details.unit .n{flex:none;width:34px;height:34px;border-radius:50%;
  background:var(--navy);color:#fff;display:grid;place-items:center;
  font-weight:700;font-size:.85rem}
details.unit .t{flex:1;font-weight:700}
details.unit summary::after{content:"﹀";color:var(--muted);transition:transform .2s}
details.unit[open] summary::after{transform:rotate(180deg)}
details.unit .body{padding:2px 20px 16px 20px;border-top:1px dashed var(--line)}
details.unit .lead{color:var(--muted);margin:12px 0 8px;font-size:.95rem}
details.unit ol,details.unit ul{margin:6px 0 0;padding-inline-start:22px}
details.unit li{margin-bottom:5px}

/* الأسئلة */
ol.q{list-style:none;margin:0;padding:0;display:grid;gap:16px;counter-reset:q}
ol.q>li{counter-increment:q}
.qcard{padding:20px 22px}
.qhead{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
.qnum{background:var(--navy);color:#fff;border-radius:50%;width:32px;height:32px;
  display:grid;place-items:center;font-size:.9rem;font-weight:700;flex:none}
.qnum::before{content:counter(q)}
.qtext{font-weight:700;font-size:1.05rem;margin:0 0 12px}
ul.opts{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:8px}
ul.opts li.opt{display:flex;gap:10px;align-items:flex-start;cursor:pointer;
  border:1.5px solid var(--line);border-radius:12px;padding:10px 14px;
  transition:border-color .15s,background .15s}
ul.opts li.opt:hover{border-color:var(--brand);background:var(--soft)}
ul.opts li.opt .k{flex:none;width:26px;height:26px;border-radius:50%;
  background:var(--soft);color:var(--brand);display:grid;place-items:center;
  font-size:.8rem;font-weight:700}
ul.opts li.opt.right{border-color:var(--ok);background:var(--ok-soft)}
ul.opts li.opt.right .k{background:var(--ok);color:#fff}
ul.opts li.opt.wrong{border-color:var(--bad);background:var(--bad-soft)}
ul.opts li.opt.wrong .k{background:var(--bad);color:#fff}
.answer{background:var(--ok-soft);border:1px solid var(--ok);
  border-radius:12px;padding:12px 16px;font-size:.95rem}
.answer b{color:var(--ok)}
.why{color:var(--muted);font-size:.9rem;margin-top:6px}

/* المصطلحات */
.terms{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.term{background:var(--card);border:1px solid var(--line);
  border-inline-start:5px solid var(--accent);border-radius:12px;padding:12px 16px}
.term h3{margin:0 0 4px;font-size:1rem;direction:auto;unicode-bidi:plaintext}
.term p{margin:0;color:var(--muted);font-size:.92rem}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;
  padding:3px 14px;font-size:.9rem;direction:auto;unicode-bidi:plaintext}
.chip.more{display:none}
.chips.open .chip.more{display:inline-block}
.note-sm{color:var(--muted);font-size:.88rem;margin:4px 0 0}

/* بطاقات المراجعة */
.deck{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.flash{perspective:900px;height:170px;cursor:pointer;border:0;background:none;padding:0}
.flash .face{position:absolute;inset:0;display:grid;place-items:center;
  text-align:center;padding:16px 18px;border-radius:var(--radius);
  backface-visibility:hidden;transition:transform .45s;box-shadow:var(--shadow)}
.flash .front{background:var(--card);border:1px solid var(--line);font-weight:700}
.flash .back{background:var(--navy);color:#fff;transform:rotateY(180deg);
  font-size:.95rem}
.flash{position:relative;transform-style:preserve-3d}
.flash.flip .front{transform:rotateY(-180deg)}
.flash.flip .back{transform:rotateY(0)}
.flash .hint{position:absolute;bottom:8px;inset-inline:0;text-align:center;
  font-size:.72rem;color:var(--muted);z-index:2;pointer-events:none}

details.dropped{margin-top:40px;font-size:.88rem;color:var(--muted)}
details.dropped summary{cursor:pointer}
details.dropped ul{padding-inline-start:22px}

/* مشغّل التسجيل العائم */
.mini{position:fixed;left:16px;bottom:16px;width:min(360px,calc(100vw - 32px));
  z-index:40;background:#000;border-radius:14px;overflow:hidden;
  box-shadow:0 12px 40px rgba(0,0,0,.35)}
.mini video{width:100%;display:block}
.mini button{position:absolute;top:6px;right:6px;z-index:2;border:0;
  background:rgba(0,0,0,.6);color:#fff;border-radius:50%;width:28px;height:28px;
  cursor:pointer}

footer.end{margin-top:56px;text-align:center;color:var(--muted);font-size:.82rem}

@media (max-width:640px){
  .hero{padding:30px 0 26px}
  .card,.qcard{padding:16px}
  .terms{grid-template-columns:1fr}
}
@media print{
  .bar,.mini,.btn,.ts,.flash .hint{display:none!important}
  body{background:#fff;font-size:11.5pt}
  .hero{background:#fff!important;color:#000;padding:0 0 12px;
    border-bottom:3px solid #2d2e82}
  .hero::before,.hero::after{display:none}
  .stat{background:#fff;border-color:#ccc;color:#000}
  .answer{display:block!important}
  .card,.qcard,details.unit,.term{break-inside:avoid;box-shadow:none;border-color:#ccc}
  details.unit>.body{display:block}
  .flash{height:auto}
  .flash .face{position:static;transform:none!important;backface-visibility:visible;
    display:block;box-shadow:none;border:1px solid #ccc;color:#000;background:#fff}
  .flash .back{margin-top:-1px}
}
"""

_JS = """
(function(){
  var video=document.getElementById('vid'), mini=document.getElementById('mini');
  var pick=document.getElementById('pick'), pending=null;
  function seek(t){
    video.currentTime=t; video.play().catch(function(){});
  }
  if(pick) pick.addEventListener('change',function(){
    var f=pick.files&&pick.files[0]; if(!f) return;
    video.src=URL.createObjectURL(f);
    mini.classList.remove('hidden');
    if(pending!==null){ var t=pending; pending=null;
      video.addEventListener('loadedmetadata',function once(){
        video.removeEventListener('loadedmetadata',once); seek(t); }); }
  });
  function fold(s){
    return (s||'').normalize('NFC').trim().toLowerCase()
      .replace(/[\\u0610-\\u061A\\u064B-\\u065F\\u0670]/g,'')
      .replace(/[\\u0623\\u0625\\u0622\\u0671]/g,'\\u0627')
      .replace(/\\u0629/g,'\\u0647').replace(/\\u0649/g,'\\u064A')
      .replace(/[^\\w\\s\\u0600-\\u06FF]/g,' ').replace(/\\s+/g,' ').trim();
  }
  function setReveal(btn,show){
    var box=document.getElementById(btn.getAttribute('data-for'));
    box.classList.toggle('hidden',!show);
    btn.textContent=show?'إخفاء الإجابة':'إظهار الإجابة';
  }
  document.addEventListener('click',function(e){
    var t=e.target;
    var close=t.closest('#miniClose');
    if(close){ video.pause(); mini.classList.add('hidden'); return; }
    if(t.closest('#linkVideo')){ pick.click(); return; }
    if(t.closest('#printBtn')){ window.print(); return; }
    var r=t.closest('button.reveal');
    if(r){ setReveal(r, document.getElementById(r.getAttribute('data-for')).classList.contains('hidden')); return; }
    var opt=t.closest('li.opt');
    if(opt){
      var card=opt.closest('.qcard'), ans=card.getAttribute('data-answer');
      [].forEach.call(card.querySelectorAll('li.opt'),function(o){
        o.classList.remove('wrong','right');
        if(fold(o.getAttribute('data-v'))===fold(ans)) o.classList.add('right');
      });
      if(!opt.classList.contains('right')) opt.classList.add('wrong');
      var btn=card.querySelector('button.reveal'); if(btn) setReveal(btn,true);
      return;
    }
    var fl=t.closest('.flash');
    if(fl){ fl.classList.toggle('flip'); return; }
    var more=t.closest('#chipsMore');
    if(more){ var box=document.getElementById('chipsBox'); box.classList.add('open'); more.remove(); return; }
    var b=t.closest('.ts'); if(!b) return;
    var sec=parseFloat(b.getAttribute('data-t')||'0');
    if(mini.classList.contains('hidden')){ pending=sec; pick.click(); }
    else seek(sec);
  });
  var all=document.getElementById('revealAll');
  if(all) all.addEventListener('click',function(){
    var btns=[].slice.call(document.querySelectorAll('button.reveal'));
    var show=btns.some(function(b){return document.getElementById(b.getAttribute('data-for')).classList.contains('hidden');});
    btns.forEach(function(b){ setReveal(b,show); });
    all.textContent=show?'إخفاء كل الإجابات':'إظهار كل الإجابات';
  });
  var links=[].slice.call(document.querySelectorAll('a.nav'));
  if('IntersectionObserver' in window){
    var obs=new IntersectionObserver(function(es){
      es.forEach(function(en){ if(en.isIntersecting){
        links.forEach(function(a){ a.classList.toggle('on',a.getAttribute('href')==='#'+en.target.id); });
      }});
    },{rootMargin:'-20% 0px -70% 0px'});
    [].forEach.call(document.querySelectorAll('h2.sec[id]'),function(h){ obs.observe(h); });
  }
})();
"""


def _esc(text: str) -> str:
    return esc(text)


def _stamp(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    return (f'<button class="ts" data-t="{float(seconds):.3f}" '
            f'title="اسمع المقطع الذي بُني منه هذا">'
            f'{_esc(seconds_to_display(seconds))}</button>')


def _heading(anchor: str, title: str, count: Optional[int] = None) -> str:
    badge = f'<span class="count">{count}</span>' if count else ""
    return f'<h2 class="sec" id="{anchor}">{_esc(title)}{badge}</h2>'


def _objectives(pack: StudyPack) -> str:
    if not pack.objectives:
        return ""
    items = "".join(
        f"<li><span>{_esc(o.text)} {_stamp(o.source_start)}</span></li>"
        for o in pack.objectives)
    return (_heading("goals", "أهداف التعلّم", len(pack.objectives))
            + f'<ul class="obj">{items}</ul>')


def _review_map(plan: Optional[DocumentPlan]) -> str:
    """خريطة المراجعة: كل قسم من الخطة بنقاطه — تعمل بلا نموذج لغوي."""
    if plan is None or not plan.sections:
        return ""
    from document.exporters.pptx_export import section_bullets

    units: List[str] = []
    for number, section in enumerate(plan.sections, start=1):
        # صفحة قراءة لا شريحة: الخطوة تُعرض كاملة دون اقتطاع
        bullets = section_bullets(section, max_chars=600, max_items=20)
        has_steps = any(b.kind == "step" for b in section.blocks)
        shots = sum(1 for b in section.blocks
                    if b.kind == "figure" and b.image_filename)
        lead = (f'<p class="lead">{_esc(section.summary)}</p>'
                if section.summary else "")
        if bullets:
            tag = "ol" if has_steps and all(D.split_step(b)[0] for b in bullets) else "ul"
            lines = "".join(f"<li>{_esc(D.split_step(b)[1] or b)}</li>"
                            for b in bullets)
            points = f"<{tag}>{lines}</{tag}>"
        else:
            points = ""
        if not (lead or points):
            continue
        meta = "".join([
            f'<span class="tag">{D.ar_count(shots, "figure")}</span>' if shots else "",
            _stamp(section.start_timestamp)])
        units.append(
            '<details class="unit"><summary>'
            f'<span class="n">{number}</span>'
            f'<span class="t">{_esc(section.title)}</span>{meta}</summary>'
            f'<div class="body">{lead}{points}</div></details>')
    if not units:
        return ""
    return (_heading("map", "خريطة المراجعة", len(units))
            + f'<div class="map">{"".join(units)}</div>')


def _glossary(pack: StudyPack) -> str:
    if not pack.glossary:
        return ""
    defined = [t for t in pack.glossary if (t.definition or "").strip()]
    bare = [t for t in pack.glossary if not (t.definition or "").strip()]
    out = [_heading("terms", "المصطلحات", len(pack.glossary))]
    if defined:
        cards = "".join(
            f'<div class="term"><h3>{_esc(t.term)}</h3>'
            f'<p>{_esc(t.definition)}</p>'
            f'<div style="margin-top:6px"><span class="tag">'
            f'{_ORIGIN_LABEL.get(t.origin, t.origin)}</span> '
            f'{_stamp(t.source_start)}</div></div>' for t in defined)
        out.append(f'<div class="terms">{cards}</div>')
    if bare:
        visible, rest = bare[:_CHIPS_VISIBLE], bare[_CHIPS_VISIBLE:]
        chips = "".join(f'<span class="chip">{_esc(t.term)}</span>' for t in visible)
        chips += "".join(f'<span class="chip more">{_esc(t.term)}</span>'
                         for t in rest)
        more = (f'<button class="btn" id="chipsMore">عرض {len(rest)} مصطلحًا آخر'
                '</button>' if rest else "")
        out.append(
            '<div class="card" style="margin-top:14px">'
            '<strong>مصطلحات وردت في المادة</strong>'
            '<p class="note-sm">قائمة بما ظهر على الشاشة أو تكرّر في الشرح '
            '— للمراجعة والبحث، دون تعريفات.</p>'
            f'<div class="chips" id="chipsBox">{chips}</div>{more}</div>')
    return "".join(out)


def _questions(pack: StudyPack) -> str:
    if not pack.questions:
        return ""
    items: List[str] = []
    for index, question in enumerate(pack.questions, start=1):
        box_id = f"a{index}"
        options = ""
        if question.kind == "mcq" and question.options:
            letters = "أبجدهوزحطي"
            options = ('<ul class="opts">' + "".join(
                f'<li class="opt" tabindex="0" data-v="{_esc(o)}">'
                f'<span class="k">{letters[i] if i < len(letters) else i + 1}</span>'
                f'<span>{_esc(o)}</span></li>'
                for i, o in enumerate(question.options)) + "</ul>")
        why = (f'<div class="why">{_esc(question.explanation)}</div>'
               if question.explanation else "")
        level = _LEVEL_LABEL.get(question.difficulty, "")
        items.append(
            f'<li><div class="card qcard" data-answer="{_esc(question.answer)}">'
            '<div class="qhead"><span class="qnum"></span>'
            f'<span class="tag">{_KIND_LABEL.get(question.kind, question.kind)}</span>'
            + (f'<span class="tag">{level}</span>' if level else "")
            + f'{_stamp(question.source_start)}</div>'
            f'<p class="qtext">{_esc(question.question)}</p>{options}'
            f'<button class="btn reveal" data-for="{box_id}">إظهار الإجابة</button>'
            f'<div class="answer hidden" id="{box_id}" style="margin-top:10px">'
            f'<b>الإجابة:</b> {_esc(question.answer)}{why}</div>'
            "</div></li>")
    return (_heading("quiz", "اختبر نفسك", len(pack.questions))
            + f'<ol class="q">{"".join(items)}</ol>')


def _flashcards(pack: StudyPack) -> str:
    if not pack.flashcards:
        return ""
    cards = "".join(
        '<button class="flash" aria-label="اقلب البطاقة">'
        f'<span class="face front">{_esc(c.front)}</span>'
        f'<span class="face back">{_esc(c.back)}</span>'
        '<span class="hint">اضغط للقلب</span></button>'
        for c in pack.flashcards)
    return (_heading("cards", "بطاقات المراجعة", len(pack.flashcards))
            + f'<div class="deck">{cards}</div>')


def _dropped(pack: StudyPack) -> str:
    if not pack.dropped:
        return ""
    # الشفافية هنا ليست زينة: المعلّم الذي يرى عشرة أسئلة ولا يعرف أن
    # ستّة رُفضت سيظنّ الأداة ضعيفة، والحقيقة أنها كانت دقيقة.
    items = "".join(f"<li>{_esc(reason)}</li>" for reason in pack.dropped[:60])
    return ('<details class="dropped"><summary>'
            f'أُسقط {len(pack.dropped)} عنصرًا في التحقّق — اضغط للتفاصيل'
            f'</summary><ul>{items}</ul></details>')


def _stats(pack: StudyPack, plan: Optional[DocumentPlan]) -> str:
    cells = []
    if plan is not None and plan.sections:
        cells.append((len(plan.sections), D.ar_noun(len(plan.sections), "section")))
    for count, kind in ((len(pack.objectives), "objective"),
                        (len(pack.questions), "question"),
                        (len(pack.glossary), "term"),
                        (len(pack.flashcards), "card")):
        if count:
            cells.append((count, D.ar_noun(count, kind)))
    return ('<div class="stats">' + "".join(
        f'<div class="stat"><b>{n}</b><span>{label}</span></div>'
        for n, label in cells) + "</div>") if cells else ""


def render_study_html(pack: StudyPack, subtitle: str = "",
                      plan: Optional[DocumentPlan] = None) -> str:
    title = _esc(pack.title or (plan.title if plan else "") or "دليل المذاكرة")
    sections = [
        ("goals", "الأهداف", _objectives(pack)),
        ("map", "خريطة المراجعة", _review_map(plan)),
        ("quiz", "اختبر نفسك", _questions(pack)),
        ("terms", "المصطلحات", _glossary(pack)),
        ("cards", "البطاقات", _flashcards(pack)),
    ]
    nav = "".join(f'<a class="nav" href="#{anchor}">{label}</a>'
                  for anchor, label, body in sections if body)
    reveal = ('<button class="btn" id="revealAll">إظهار كل الإجابات</button>'
              if pack.questions else "")
    body = "".join(part for _, _, part in sections)
    return (
        "<!doctype html>\n"
        '<html lang="ar" dir="rtl">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>دليل المذاكرة — {title}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        '<header class="hero"><div class="in">'
        '<span class="kicker">دليل المذاكرة</span>'
        f"<h1>{title}</h1>"
        + (f'<p class="sub">{_esc(subtitle)}</p>' if subtitle else "")
        + _stats(pack, plan) + "</div></header>\n"
        f'<nav class="bar" aria-label="أقسام الدليل"><div class="in">{nav}'
        '<span class="spacer"></span>' + reveal +
        '<button class="btn" id="linkVideo" title="يبقى الملف على جهازك — '
        'لا يُنسخ ولا يُرفع">ربط التسجيل</button>'
        '<button class="btn" id="printBtn">طباعة</button></div></nav>\n'
        f'<main class="wrap">{body}{_dropped(pack)}'
        '<footer class="end">أُعدّ هذا الدليل آليًّا من التسجيل؛ راجع المقطع '
        'المرتبط بكل عنصر للتحقّق منه.</footer></main>\n'
        '<aside class="mini hidden" id="mini"><button id="miniClose" '
        'aria-label="إغلاق المشغّل">✕</button>'
        '<video id="vid" controls preload="metadata"></video></aside>\n'
        '<input id="pick" type="file" accept="video/*,audio/*" class="hidden">\n'
        f"<script>{_JS}</script>\n</body>\n</html>\n")


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
    path.write_text(render_study_html(pack, ctx.plan.subtitle, ctx.plan),
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
