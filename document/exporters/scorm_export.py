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
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from config.schemas import DocumentPlan, StudyPack
from document.exporters import ExportContext, register
from utils.logger import logger

#: درجة النجاح المُبلَّغة للمنصّة. ‏70٪ عرفٌ شائع في SCORM 1.2، والمنصّة
#: تستطيع تجاوزه من إعدادها — لكن غيابه يجعلها تعتبر كل محاولة ناجحة.
MASTERY_SCORE = 70

_LAUNCHER_CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#15161c;--muted:#5b6070;--line:#e3e5ee;
  --brand:#2D2E82;--soft:#ececf7;--ok:#0f7b4f;--ok-soft:#e6f5ee;
  --bad:#b3261e;--bad-soft:#fbe9e7}
@media (prefers-color-scheme:dark){:root{--bg:#14151c;--card:#1c1e28;
  --ink:#eceef6;--muted:#9aa0b4;--line:#2b2e3c;--brand:#8f90e8;--soft:#23253a;
  --ok:#5ed6a4;--ok-soft:#18342a;--bad:#ff8a80;--bad-soft:#3a1d1a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);direction:rtl;font-size:16px;
  line-height:1.9;font-family:"Segoe UI","Tahoma","Noto Naskh Arabic","Arial",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:0 18px 70px}
header.m{background:var(--brand);color:#fff;padding:22px 18px;margin-bottom:20px}
header.m .in{max-width:820px;margin:0 auto}
header.m h1{margin:0;font-size:1.4rem}
h2{font-size:1.12rem;color:var(--brand);border-bottom:2px solid var(--soft);
  padding-bottom:7px;margin:26px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:15px 17px;margin-bottom:12px}
a.doc{display:inline-block;margin:0 0 8px 10px;padding:9px 16px;border-radius:9px;
  background:var(--soft);color:var(--brand);text-decoration:none;font-size:.92rem}
a.doc:hover{outline:1px solid var(--brand)}
ul.obj{margin:0;padding-inline-start:20px}
ol.q{list-style:none;counter-reset:q;margin:0;padding:0}
ol.q>li{counter-increment:q}
.qtext{font-weight:600;margin:0 0 9px}
.qtext::before{content:counter(q) ". ";color:var(--brand)}
label.opt{display:block;border:1px solid var(--line);border-radius:8px;
  padding:8px 12px;margin-bottom:6px;cursor:pointer}
label.opt:hover{border-color:var(--brand)}
label.opt input{margin-inline-end:8px}
input.short{width:100%;font:inherit;padding:9px 12px;border:1px solid var(--line);
  border-radius:8px;background:var(--bg);color:var(--ink)}
button.submit{font:inherit;font-size:1rem;background:var(--brand);color:#fff;
  border:0;border-radius:10px;padding:11px 26px;cursor:pointer}
button.submit:disabled{opacity:.55;cursor:default}
.verdict{margin-top:10px;border-radius:9px;padding:9px 12px;font-size:.92rem}
.right{background:var(--ok-soft);border:1px solid var(--ok)}
.wrong{background:var(--bad-soft);border:1px solid var(--bad)}
#result{font-size:1.05rem;font-weight:600;margin-top:16px}
.note{color:var(--muted);font-size:.85rem}
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
  _ready = a.LMSInitialize("") === "true" || a.LMSInitialize("") === true;
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


def render_launcher(plan: DocumentPlan, pack: Optional[StudyPack],
                    extra_pages: List[tuple]) -> str:
    title = _esc(plan.title or "وحدة تعليمية")
    objectives = ""
    if pack and pack.objectives:
        objectives = ('<h2>أهداف الوحدة</h2><div class="card"><ul class="obj">'
                      + "".join(f"<li>{_esc(o.text)}</li>"
                                for o in pack.objectives)
                      + "</ul></div>")
    links = "".join(f'<a class="doc" href="{_esc(href)}" target="_blank">{_esc(label)}</a>'
                    for label, href in extra_pages)
    links_block = (f'<h2>مواد الوحدة</h2><div class="card">{links}</div>'
                   if links else "")
    return (
        "<!doctype html>\n"
        '<html lang="ar" dir="rtl">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{_LAUNCHER_CSS}</style>\n</head>\n"
        f'<body>\n<header class="m"><div class="in"><h1>{title}</h1></div></header>\n'
        f'<div class="wrap">{objectives}{links_block}'
        '<h2>أسئلة التقييم</h2>'
        f'{_render_quiz(pack)}</div>\n'
        f"<script>{_SCORM_JS}{_quiz_js(MASTERY_SCORE)}</script>\n"
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
    from document.exporters.html_export import render_html
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

        # صفحة المحتوى الكاملة — الصور مضمّنة فيها، فلا ملفّات صور
        # منفصلة في الحزمة ولا مسارات تنكسر داخل إطار المنصّة.
        (staging / "content.html").write_text(render_html(ctx),
                                              encoding="utf-8")
        pages.append(("المحتوى الكامل", "content.html"))

        if pack is not None and not pack.is_empty():
            (staging / "study.html").write_text(
                render_study_html(pack, ctx.plan.subtitle), encoding="utf-8")
            pages.append(("دليل المذاكرة", "study.html"))

        (staging / "index.html").write_text(
            render_launcher(ctx.plan, pack, pages), encoding="utf-8")

        names = sorted(path.name for path in staging.iterdir())
        (staging / "imsmanifest.xml").write_text(
            build_manifest(ctx.plan.title or ctx.base_path.stem, names),
            encoding="utf-8")

        # الكتابة إلى ملفّ مؤقّت ثم النقل: حزمةٌ نصفُ مكتوبة في مجلد
        # المستخدم تُرفع إلى المنصّة فتُرفض بلا سبب مفهوم.
        bundle = staging.parent / "package.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            # ‏imsmanifest.xml **في جذر** الملفّ المضغوط لا في مجلد
            # داخله. أشهر سبب لرفض الحزمة هو ضغط المجلد نفسه.
            for path in sorted(staging.iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
        shutil.move(str(bundle), str(target))

    logger.info(f"حزمة SCORM 1.2: {target.name}")
    return target


register("scorm", export)
