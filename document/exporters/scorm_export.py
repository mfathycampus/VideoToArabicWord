"""حزمة SCORM 1.2 — الفرق بين «برنامج» و«منتج مدرسي».

منصّة التعلّم في أي مدرسة أو شركة (‏Moodle، Blackboard، Schoology،
Canvas) لا تقبل ملفًّا يُرفع ويُقرأ؛ تقبل **وحدة تعلّم** تعرف من فتحها
ومتى أتمّها وبأي درجة. وذلك هو SCORM: ملفّ ZIP فيه بيانٌ وصفيّ
(``imsmanifest.xml``) ومحتوًى يتحدّث إلى المنصّة عبر واجهة متّفق عليها.

**ولماذا 1.2 لا 2004 ولا xAPI.** ‏SCORM 1.2 هو الصيغة الوحيدة التي
تقبلها **كل** المنصّات الأربع بلا إعداد إضافي. و2004 أغنى ولا تدعمه
منصّات قائمة كثيرة، وxAPI يحتاج خادم LRS منفصلًا — أي بنيةً تحتية
جديدة، وهو بالضبط ما يجعل المدرسة تؤجّل القرار. والهدف هنا أن يرفع
المعلّم ملفًّا واحدًا فيعمل.

**وما يُبلَّغ للمنصّة مقصود ومحدود:**

* ``lesson_status`` — أتمّ الطالب الوحدة أم لا
* ``score.raw`` — درجته في أسئلة الحزمة التعليمية، إن وُجدت
* ``session_time`` — كم مكث

ولا يُرسَل شيء آخر. والمحتوى كلّه داخل الحزمة، فلا اتصال بالإنترنت ولا
مرجع خارجي — وهو شرط عملي: منصّات كثيرة تعمل خلف جدار ناريّ يمنع
الموارد الخارجية، فحزمةٌ تعتمد على CDN تخرج بيضاء عند الطالب وحده.
"""
from __future__ import annotations

import html
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from config.schemas import DocumentPlan, StudyPack
from document.exporters import ExportContext, register
from document.exporters import _design as D
from document.exporters._web import base_css
from utils.logger import logger
from utils.timestamps import seconds_to_display

#: درجة النجاح المُبلَّغة للمنصّة. ‏70٪ عرفٌ شائع في SCORM 1.2، والمنصّة
#: تستطيع تجاوزه من إعدادها — لكن غيابه يجعلها تعتبر كل محاولة ناجحة.
MASTERY_SCORE = 70

#: صورة أكبر من هذا تُصغَّر عند نسخها للحزمة؛ دونه تُنسخ كما هي بلا إعادة ضغط.
MEDIA_COPY_LIMIT = 400 * 1024
MEDIA_MAX_WIDTH = 1600

_PLAYER_CSS = base_css() + """
body{padding-top:0}
.top{position:sticky;top:0;z-index:30;background:var(--navy);color:#fff;
  box-shadow:0 2px 12px rgba(0,0,0,.25)}
.top .row{display:flex;align-items:center;gap:14px;padding:10px 18px}
.top h1{flex:1;margin:0;font-size:1.02rem;line-height:1.4;font-weight:700;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.top .menu{display:none;background:rgba(255,255,255,.14);border:0;color:#fff;
  border-radius:8px;padding:5px 12px;cursor:pointer}
.pct{font-size:.82rem;opacity:.9;font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{height:5px;background:rgba(255,255,255,.18)}
.bar i{display:block;height:100%;width:0;background:var(--accent);
  transition:width .35s}
.shell{display:grid;grid-template-columns:310px minmax(0,1fr);gap:26px;
  max-width:1280px;margin:0 auto;padding:22px 18px 70px}
aside.toc{position:sticky;top:78px;align-self:start;max-height:calc(100vh - 96px);
  overflow:auto;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);padding:10px}
.toc ol{list-style:none;margin:0;padding:0}
.toc button{width:100%;text-align:right;display:flex;gap:10px;align-items:flex-start;
  background:none;border:0;border-radius:10px;padding:8px 10px;cursor:pointer;
  font-size:.9rem;line-height:1.6}
.toc button:hover{background:var(--soft)}
.toc button.cur{background:var(--soft);font-weight:700;color:var(--brand)}
.toc .dot{flex:none;width:24px;height:24px;border-radius:50%;margin-top:1px;
  border:2px solid var(--line);display:grid;place-items:center;font-size:.72rem;
  font-weight:700;color:var(--muted)}
.toc button.done .dot{background:var(--ok);border-color:var(--ok);color:#fff}
.toc button.cur .dot{border-color:var(--brand);color:var(--brand)}
.toc button.done.cur .dot{color:#fff}
main.stage{min-width:0}
section.unit{display:none}
section.unit.on{display:block;animation:fade .25s}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1}}
section.unit>h2{display:flex;gap:12px;align-items:center;margin:0 0 6px;
  font-size:1.5rem;line-height:1.5;color:var(--navy)}
@media (prefers-color-scheme:dark){section.unit>h2{color:var(--ink)}}
section.unit>h2 .num{flex:none;background:var(--accent);color:#2a1d00;
  font-size:.9rem;border-radius:999px;padding:0 12px;font-weight:800}
.summary{color:var(--muted);margin:0 0 18px}
p.para{margin:0 0 14px}
ul.bullets{margin:0 0 14px;padding-inline-start:22px}
.step{display:flex;gap:12px;align-items:flex-start;margin:0 0 12px;
  background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:10px 14px}
.step .n{flex:none;width:30px;height:30px;border-radius:50%;background:var(--navy);
  color:#fff;font-weight:700;font-size:.9rem;display:grid;place-items:center}
.step p{margin:2px 0 0}
.note{margin:0 0 14px;padding:10px 14px;border-inline-start:5px solid var(--accent);
  background:var(--accent-soft);color:var(--accent-ink);border-radius:10px}
blockquote{margin:0 0 14px;padding:10px 16px;border-inline-start:4px solid var(--brand);
  background:var(--soft);border-radius:10px}
figure{margin:6px 0 22px;background:var(--card);border:1px solid var(--line);
  border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
figure img{display:block;width:100%;height:auto;cursor:zoom-in;background:#fff}
figcaption{padding:10px 16px;font-size:.9rem;color:var(--muted);
  border-top:1px solid var(--line);display:flex;gap:10px;justify-content:space-between}
details.ocr{display:none}
.nav{display:flex;justify-content:space-between;gap:12px;margin-top:28px}
.zoom{position:fixed;inset:0;z-index:60;background:rgba(8,9,24,.92);display:none;
  place-items:center;padding:20px;cursor:zoom-out}
.zoom.on{display:grid}
.zoom img{max-width:100%;max-height:100%;border-radius:8px;background:#fff}
/* الأسئلة */
ol.q{list-style:none;counter-reset:q;margin:0 0 16px;padding:0;display:grid;gap:14px}
ol.q>li{counter-increment:q}
.qcard{padding:18px 20px}
.qtext{font-weight:700;margin:0 0 10px}
.qtext::before{content:counter(q) ". ";color:var(--brand)}
label.opt{display:block;border:1.5px solid var(--line);border-radius:12px;
  padding:9px 14px;margin-bottom:8px;cursor:pointer}
label.opt:hover{border-color:var(--brand);background:var(--soft)}
label.opt input{margin-inline-end:8px}
input.short{width:100%;font:inherit;padding:10px 14px;border:1.5px solid var(--line);
  border-radius:12px;background:var(--bg);color:var(--ink)}
.verdict{margin-top:10px;border-radius:10px;padding:9px 14px;font-size:.92rem}
.right{background:var(--ok-soft);border:1px solid var(--ok)}
.wrong{background:var(--bad-soft);border:1px solid var(--bad)}
#result{font-size:1.15rem;font-weight:700;margin-top:16px}
.done-box{text-align:center;padding:30px 20px}
.done-box .big{font-size:2.6rem}
.terms{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.term{background:var(--card);border:1px solid var(--line);
  border-inline-start:5px solid var(--accent);border-radius:12px;padding:10px 14px}
.term b{direction:auto;unicode-bidi:plaintext;display:block}
.term span{color:var(--muted);font-size:.9rem}
.muted{color:var(--muted);font-size:.9rem}
@media (max-width:900px){
  .shell{grid-template-columns:1fr;padding-top:14px}
  aside.toc{position:fixed;inset:0 0 0 auto;width:min(86vw,340px);z-index:50;
    max-height:none;border-radius:0;transform:translateX(100%);transition:transform .25s}
  aside.toc.open{transform:none}
  .top .menu{display:block}
}
"""


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _xml(text: str) -> str:
    return html.escape(text or "", quote=True)


# ---------------------------------------------------------------------
# واجهة SCORM — اكتشافها هو أكثر ما يُخطئ فيه الناشرون
# ---------------------------------------------------------------------
_SCORM_JS = """
// المنصّة تضع كائن API في نافذةٍ ما فوق إطار المحتوى — وموضعه يختلف
// بين Moodle وBlackboard وغيرهما. البحث يصعد سلسلة الآباء ثم يجرّب
// نافذة الفتح، وهو ما نصّت عليه ADL نفسها. الاكتفاء بـ``parent.API``
// هو سبب أشهر عطل: حزمةٌ تعمل عند الناشر وتصمت عند المدرسة.
var _api = null, _ready = false;
function _find(win){
  var hops = 0;
  while (win && !win.API && win.parent && win.parent !== win && hops++ < 500)
    win = win.parent;
  return win ? win.API : null;
}
function api(){
  if (_api) return _api;
  _api = _find(window);
  if (!_api && window.opener) _api = _find(window.opener);
  return _api;
}
function set(key, value){ var a = api(); return a ? a.LMSSetValue(key, String(value)) : false; }
function scormInit(){
  var a = api();
  if (!a) return false;                    // تشغيل خارج منصّة: الصفحة تعمل كما هي
  var r = a.LMSInitialize("");
  _ready = (r === "true" || r === true);
  if (a.LMSGetValue("cmi.core.lesson_status") === "not attempted")
    set("cmi.core.lesson_status", "incomplete");
  a.LMSCommit("");
  return _ready;
}
function _hhmmss(ms){
  var s = Math.floor(ms/1000);
  return [Math.floor(s/3600), Math.floor(s/60)%60, s%60]
    .map(function(n){ return (n<10?"0":"") + n; }).join(":");
}
var _opened = Date.now();
function scormFinish(){
  var a = api(); if (!a) return;
  set("cmi.core.session_time", _hhmmss(Date.now() - _opened));
  a.LMSCommit(""); a.LMSFinish("");
}
// ``unload`` لا ``beforeunload``: الأخير لا يقع في بعض المنصّات التي
// تُزيل الإطار برمجيًّا، فتضيع الدرجة بعد أن أجاب الطالب فعلًا.
window.addEventListener("unload", scormFinish);
"""


def _quiz_js(mastery: int) -> str:
    return """
function grade(){
  var items = [].slice.call(document.querySelectorAll('[data-answer]'));
  var right = 0;
  items.forEach(function(item){
    var expected = item.getAttribute('data-answer');
    var given = '';
    var picked = item.querySelector('input[type=radio]:checked');
    var typed = item.querySelector('input.short');
    if (picked) given = picked.value;
    else if (typed) given = typed.value;
    var ok = fold(given) === fold(expected);
    if (ok) right++;
    var box = item.querySelector('.verdict');
    box.className = 'verdict ' + (ok ? 'right' : 'wrong');
    // الصندوق مخفيّ بنمط سطريّ، والصنف وحده لا يكشفه — عطلٌ يجعل
    // التصحيح يبدو كأنه لم يقع أصلًا.
    box.style.display = 'block';
    box.textContent = (ok ? '\\u2713 صحيحة' : '\\u2717 الإجابة: ' + expected)
      + (item.getAttribute('data-why') ? ' — ' + item.getAttribute('data-why') : '');
  });
  var score = items.length ? Math.round(right * 100 / items.length) : 100;
  var out = document.getElementById('result');
  out.textContent = 'نتيجتك: ' + right + ' من ' + items.length
    + '  (' + score + '%)';
  set('cmi.core.score.raw', score);
  set('cmi.core.score.min', 0);
  set('cmi.core.score.max', 100);
  set('cmi.core.lesson_status', score >= MASTERY ? 'passed' : 'failed');
  var a = api(); if (a) a.LMSCommit('');
  document.getElementById('submit').disabled = true;
}
// نفس تطبيع ai/study_verify: همزة الألف والتاء المربوطة والتشكيل
// فروقٌ لا يراها الطالب، وحسمُ إجابته عليها ظلم.
function fold(s){
  return (s||'').normalize('NFC').trim().toLowerCase()
    .replace(/[\\u0610-\\u061A\\u064B-\\u065F\\u0670]/g,'')
    .replace(/[\\u0623\\u0625\\u0622\\u0671]/g,'\\u0627')
    .replace(/\\u0629/g,'\\u0647').replace(/\\u0649/g,'\\u064A')
    .replace(/[^\\w\\s\\u0600-\\u06FF]/g,' ').replace(/\\s+/g,' ').trim();
}
var MASTERY = __MASTERY__;
document.addEventListener('DOMContentLoaded', function(){
  scormInit();
  var b = document.getElementById('submit');
  if (b) b.addEventListener('click', grade);
  else { set('cmi.core.lesson_status','completed'); var a=api(); if(a) a.LMSCommit(''); }
});
""".replace("__MASTERY__", str(mastery))


def _render_quiz(pack: Optional[StudyPack]) -> str:
    if pack is None or not pack.questions:
        return ('<div class="card note">لا أسئلة في هذه الوحدة — '
                'تُسجَّل «مكتملة» بمجرّد فتحها.</div>')
    items: List[str] = []
    for question in pack.questions:
        why = _esc(question.explanation)
        body = ""
        if question.kind == "mcq" and question.options:
            name = f"q{id(question)}"
            body = "".join(
                f'<label class="opt"><input type="radio" name="{name}" '
                f'value="{_esc(option)}">{_esc(option)}</label>'
                for option in question.options)
        elif question.kind == "true_false":
            name = f"q{id(question)}"
            body = "".join(
                f'<label class="opt"><input type="radio" name="{name}" '
                f'value="{value}">{value}</label>' for value in ("صح", "خطأ"))
        else:
            body = '<input class="short" type="text" placeholder="اكتب إجابتك…">'
        items.append(
            f'<li><div class="card" data-answer="{_esc(question.answer)}" '
            f'data-why="{why}">'
            f'<p class="qtext">{_esc(question.question)}</p>{body}'
            '<div class="verdict" style="display:none"></div></div></li>')
    return (f'<ol class="q">{"".join(items)}</ol>'
            '<button class="submit" id="submit">صحّح وأرسل النتيجة</button>'
            '<div id="result" role="status"></div>')


_PLAYER_JS = """
var UNITS = [].slice.call(document.querySelectorAll('section.unit'));
var TOC = [].slice.call(document.querySelectorAll('aside.toc button'));
var QUIZ_UNIT = document.querySelector('section.unit[data-quiz]');
var visited = UNITS.map(function(){ return 0; });
var cur = 0;
function persist(){
  set('cmi.core.lesson_location', String(cur));
  set('cmi.suspend_data', visited.join(''));
  var a = api(); if (a) a.LMSCommit('');
}
function restore(){
  var a = api(); if (!a) return;
  var loc = parseInt(a.LMSGetValue('cmi.core.lesson_location'), 10);
  var sus = a.LMSGetValue('cmi.suspend_data') || '';
  for (var i = 0; i < visited.length && i < sus.length; i++) visited[i] = sus.charAt(i) === '1' ? 1 : 0;
  if (!isNaN(loc) && loc >= 0 && loc < UNITS.length) cur = loc;
}
function contentDone(){
  return UNITS.every(function(u, i){ return u === QUIZ_UNIT || visited[i]; });
}
function paint(){
  UNITS.forEach(function(u, i){ u.classList.toggle('on', i === cur); });
  TOC.forEach(function(b, i){
    b.classList.toggle('cur', i === cur);
    b.classList.toggle('done', !!visited[i]);
  });
  var seen = visited.reduce(function(a, b){ return a + b; }, 0);
  var pct = Math.round(seen * 100 / UNITS.length);
  document.getElementById('fill').style.width = pct + '%';
  document.getElementById('pct').textContent = seen + ' / ' + UNITS.length + '  (' + pct + '%)';
  document.getElementById('prev').disabled = cur === 0;
  document.getElementById('next').disabled = cur === UNITS.length - 1;
}
function go(i, scroll){
  if (i < 0 || i >= UNITS.length) return;
  cur = i; visited[i] = 1; paint();
  if (!QUIZ_UNIT && contentDone()) set('cmi.core.lesson_status', 'completed');
  persist();
  if (scroll !== false) window.scrollTo({top: 0, behavior: 'smooth'});
  document.getElementById('toc').classList.remove('open');
}
function boot(){
  scormInit(); restore();
  TOC.forEach(function(b, i){ b.addEventListener('click', function(){ go(i); }); });
  document.getElementById('prev').addEventListener('click', function(){ go(cur - 1); });
  document.getElementById('next').addEventListener('click', function(){ go(cur + 1); });
  document.getElementById('menu').addEventListener('click', function(){
    document.getElementById('toc').classList.toggle('open'); });
  document.addEventListener('keydown', function(e){
    if (/INPUT|TEXTAREA/.test((e.target.tagName || ''))) return;
    if (e.key === 'ArrowLeft') go(cur + 1);       // RTL: اليسار = التالي
    else if (e.key === 'ArrowRight') go(cur - 1);
    else if (e.key === 'Escape') document.getElementById('zoom').classList.remove('on');
  });
  var zoom = document.getElementById('zoom');
  document.addEventListener('click', function(e){
    var img = e.target.closest('figure img');
    if (img){ zoom.querySelector('img').src = img.src; zoom.classList.add('on'); }
    else if (e.target.closest('#zoom')) zoom.classList.remove('on');
  });
  go(cur, false);
}
"""


def _quiz_unit_js() -> str:
    """نهاية السكربت: التشغيل. المنطق نفسه سواء وُجدت أسئلة أم لا."""
    return ("document.addEventListener('DOMContentLoaded', function(){\n"
            "  boot();\n"
            "  var b = document.getElementById('submit');\n"
            "  if (b) b.addEventListener('click', grade);\n"
            "});\n")


_PAGE_BLOCK_STAMP = re.compile(r'<button class="ts"[^>]*>(.*?)</button>', re.S)


def _prepare_media(ctx: ExportContext, staging: Path) -> Dict[Path, str]:
    """ينسخ لقطات الخطة إلى ``media/`` ويعيد خريطة المسار الأصلي → المسار النسبي.

    ملفّات منفصلة لا ‏``data:``: صفحة واحدة بـ13 ميغابايت من base64 تُحمَّل
    كاملةً قبل أن يرى الطالب سطرًا، وبعض المنصّات تقتطعها.
    """
    from PIL import Image

    media = staging / "media"
    mapping: Dict[Path, str] = {}
    for section in ctx.plan.sections:
        for block in section.blocks:
            if block.kind != "figure" or not block.image_filename:
                continue
            source = ctx.images_dir / block.image_filename
            if source in mapping or not source.is_file():
                continue
            media.mkdir(exist_ok=True)
            name = f"img{len(mapping) + 1:03d}.jpg"
            try:
                if (source.suffix.lower() in (".jpg", ".jpeg")
                        and source.stat().st_size <= MEDIA_COPY_LIMIT):
                    shutil.copyfile(source, media / name)
                else:
                    with Image.open(source) as image:
                        image = image.convert("RGB")
                        if image.width > MEDIA_MAX_WIDTH:
                            ratio = MEDIA_MAX_WIDTH / image.width
                            image = image.resize(
                                (MEDIA_MAX_WIDTH, max(1, int(image.height * ratio))))
                        image.save(media / name, "JPEG", quality=80, optimize=True)
            except Exception as exc:
                logger.warning(f"تعذّر نسخ الصورة {source.name} إلى الحزمة: {exc}")
                continue
            mapping[source] = f"media/{name}"
    return mapping


def _render_chapters(ctx: ExportContext, mapping: Dict[Path, str]) -> List[tuple]:
    from document.exporters.html_export import _render_block

    def resolve(path: Path) -> Optional[str]:
        return mapping.get(path)

    chapters: List[tuple] = []
    total = len(ctx.plan.sections)
    for number, section in enumerate(ctx.plan.sections, start=1):
        step = 0
        rendered: List[str] = []
        for block in section.blocks:
            if block.kind == "step":
                step += 1
            rendered.append(_render_block(block, ctx.images_dir, resolve, step))
        body = _PAGE_BLOCK_STAMP.sub(r'<span class="tsl">\1</span>', "".join(rendered))
        summary = (section.summary or "").strip()
        html_part = (
            f'<h2><span class="num">{number}/{total}</span>{D.esc(section.title)}</h2>'
            + (f'<p class="summary">{D.esc(summary)}</p>' if summary else "")
            + (body or '<p class="muted">— لا محتوى في هذا القسم —</p>'))
        chapters.append((section.title or f"قسم {number}", html_part, "chapter"))
    return chapters


def render_player(ctx: ExportContext, pack: Optional[StudyPack],
                  chapters: List[tuple],
                  extra_pages: Optional[List[tuple]] = None) -> str:
    """صفحة المشغّل: وحدات تُعرض واحدةً واحدة، وتقدّم يُبلَّغ للمنصّة."""
    plan = ctx.plan
    title = D.esc(plan.title or "وحدة تعليمية")
    units: List[tuple] = list(chapters)

    if pack is not None and pack.objectives:
        goals = ("<h2>أهداف الوحدة</h2><ul class=\"bullets\">"
                 + "".join(f"<li>{D.esc(o.text)}</li>" for o in pack.objectives)
                 + "</ul>")
        units.insert(0, ("أهداف الوحدة", goals, "goals"))

    if pack is not None and pack.glossary:
        cards = "".join(
            f'<div class="term"><b>{D.esc(t.term)}</b>'
            + (f"<span>{D.esc(t.definition)}</span>" if (t.definition or "").strip() else "")
            + "</div>" for t in pack.glossary)
        units.append(("المصطلحات", f'<h2>المصطلحات</h2><div class="terms">{cards}</div>',
                      "terms"))

    has_quiz = pack is not None and bool(pack.questions)
    if has_quiz:
        units.append(("أسئلة التقييم",
                      "<h2>أسئلة التقييم</h2>" + _render_quiz(pack), "quiz"))
    else:
        links = "".join(
            f'<a class="btn" href="{_esc(href)}" target="_blank">{_esc(label)}</a> '
            for label, href in (extra_pages or []))
        units.append(("ختام الوحدة", (
            '<div class="card done-box"><div class="big">🎓</div>'
            "<h2 style=\"justify-content:center\">أتممت الوحدة</h2>"
            '<p class="muted">لا أسئلة في هذه الوحدة — تُسجَّل «مكتملة» '
            "بعد استعراض كل أقسامها.</p>"
            f"<p>{links}</p></div>"), "end"))

    sections = []
    toc = []
    for index, (label, body, kind) in enumerate(units):
        flag = " data-quiz" if kind == "quiz" else ""
        sections.append(f'<section class="unit"{flag} id="u{index}">{body}</section>')
        toc.append(f'<li><button type="button"><span class="dot">{index + 1}</span>'
                   f'<span>{D.esc(label)}</span></button></li>')

    extra = ""
    if has_quiz and extra_pages:
        extra = ('<p class="muted" style="margin-top:20px">مواد مساندة: '
                 + " ".join(f'<a href="{_esc(h)}" target="_blank">{_esc(l)}</a>'
                            for l, h in extra_pages) + "</p>")

    return (
        "<!doctype html>\n"
        '<html lang="ar" dir="rtl">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{_PLAYER_CSS}</style>\n</head>\n<body>\n"
        '<header class="top"><div class="row">'
        '<button class="menu" id="menu" type="button">☰ الفهرس</button>'
        f"<h1>{title}</h1><span class=\"pct\" id=\"pct\" dir=\"ltr\"></span></div>"
        '<div class="bar"><i id="fill"></i></div></header>\n'
        '<div class="shell">'
        f'<aside class="toc" id="toc" aria-label="فهرس الوحدة"><ol>{"".join(toc)}</ol></aside>\n'
        f'<main class="stage">{"".join(sections)}'
        '<div class="nav"><button class="btn" id="prev" type="button">'
        '→ السابق</button><button class="btn primary" id="next" type="button">'
        f'التالي ←</button></div>{extra}</main></div>\n'
        '<div class="zoom" id="zoom"><img alt=""></div>\n'
        f"<script>{_SCORM_JS}{_PLAYER_JS}{_quiz_js(MASTERY_SCORE)}"
        f"{_quiz_unit_js()}</script>\n"
        "</body>\n</html>\n")


# ---------------------------------------------------------------------
def build_manifest(title: str, files: List[str],
                   identifier: Optional[str] = None) -> str:
    """‏``imsmanifest.xml`` بصيغة SCORM 1.2.

    كل ملفّ في الحزمة يُذكر في ``<file>``: منصّات تتحقّق من ذلك وترفض
    الحزمة إن نقص ملفّ مذكور — أو تتجاهل الموجود غير المذكور فتخرج
    الصفحة بلا صور.
    """
    identifier = identifier or f"VTAD-{uuid.uuid4().hex[:12].upper()}"
    entries = "\n".join(f'      <file href="{_xml(name)}"/>'
                        for name in sorted(files))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="{identifier}" version="1.2"
          xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
          xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd
                              http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">
  <metadata>
    <schema>ADL SCORM</schema>
    <schemaversion>1.2</schemaversion>
  </metadata>
  <organizations default="ORG-1">
    <organization identifier="ORG-1">
      <title>{_xml(title)}</title>
      <item identifier="ITEM-1" identifierref="RES-1" isvisible="true">
        <title>{_xml(title)}</title>
        <adlcp:masteryscore>{MASTERY_SCORE}</adlcp:masteryscore>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="RES-1" type="webcontent"
              adlcp:scormtype="sco" href="index.html">
{entries}
    </resource>
  </resources>
</manifest>
"""


def export(ctx: ExportContext) -> Optional[Path]:
    from document.exporters.study_export import _load_pack, render_study_html

    if not ctx.plan.sections:
        logger.warning("خطة بلا أقسام — تُخطّى حزمة SCORM.")
        return None

    pack = _load_pack(ctx)
    target = ctx.sibling(".scorm.zip")
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vtad_scorm_") as workspace:
        staging = Path(workspace)
        pages: List[tuple] = []

        if pack is not None and not pack.is_empty():
            (staging / "study.html").write_text(
                render_study_html(pack, ctx.plan.subtitle, ctx.plan),
                encoding="utf-8")
            pages.append(("دليل المذاكرة", "study.html"))

        mapping = _prepare_media(ctx, staging)
        chapters = _render_chapters(ctx, mapping)
        (staging / "index.html").write_text(
            render_player(ctx, pack, chapters, pages), encoding="utf-8")

        names = sorted(path.relative_to(staging).as_posix()
                       for path in staging.rglob("*") if path.is_file())
        (staging / "imsmanifest.xml").write_text(
            build_manifest(ctx.plan.title or ctx.base_path.stem, names),
            encoding="utf-8")

        # الكتابة إلى ملفّ مؤقّت ثم النقل: حزمةٌ نصفُ مكتوبة في مجلد
        # المستخدم تُرفع إلى المنصّة فتُرفض بلا سبب مفهوم.
        bundle = staging.parent / "package.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            # ‏imsmanifest.xml **في جذر** الملفّ المضغوط لا في مجلد
            # داخله. أشهر سبب لرفض الحزمة هو ضغط المجلد نفسه.
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
        shutil.move(str(bundle), str(target))

    logger.info(f"حزمة SCORM 1.2: {target.name}")
    return target


register("scorm", export)
