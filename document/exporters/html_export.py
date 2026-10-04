"""صفحة ويب واحدة مستقلّة — أقوى مخرج تعليمي فعليًّا.

**لماذا تسبق PDF في الأولوية.** مستند Word ممتاز للطباعة والتحرير،
وعاجز عن ثلاثة أشياء يحتاجها المتعلّم: البحث الفوري داخل ساعتين من
الكلام، والقفز من فقرة إلى لحظتها في الفيديو، والفتح على الهاتف بلا
تثبيت شيء. هذه الصفحة تفعل الثلاثة، وتُطبع إلى PDF بضغطة من المتصفّح.

**ملف واحد بلا أي اعتماد خارجي.** لا CDN ولا خطوط شبكية ولا JavaScript
مستورد: الصور تُضمَّن كـ ``data:`` والأنماط والسكربت داخل الملف. السبب
عمليّ لا جماليّ — المعلّم يرسلها بالبريد أو يضعها على USB، ولو حملت
مرجعًا خارجيًّا واحدًا لانكسرت عند أول جهاز بلا إنترنت، وهو الجهاز الذي
بُني هذا البرنامج كلّه من أجله.

**ربط الفيديو بلا نسخه.** التسجيل قد يكون جيجابايتات؛ تضمينه عبث ونسخ
مساره يكسر عند أول نقل للملف. الحل: زرّ يختار الملف من الجهاز ويربطه
بمشغّل الصفحة عبر ``URL.createObjectURL`` — بلا رفع ولا نسخ، وكل توقيت
في الصفحة يصير نقطة قفزٍ داخله.
"""
from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path
from typing import List, Optional

from config.schemas import DocumentBlock, DocumentPlan
from document.alt_text import describe
from document.exporters import ExportContext, register
from utils.logger import logger
from utils.timestamps import seconds_to_display

#: سقف تضمين الصور. فوقه تُربط الصفحة بمجلد ``keyframes`` نسبيًّا بدل
#: نفخ الملف. الرقم مقيس على الحالة السيّئة: محاضرة ساعتين بسقف 150
#: لقطة × ~180KB ≈ 27MB خامًا، و‏base64 يضيف الثلث — ملف 36MB لا يفتحه
#: متصفّح هاتف. دون السقف تبقى الصفحة ملفًّا واحدًا قابلًا للإرسال.
MAX_EMBEDDED_IMAGE_BYTES = 12 * 1024 ** 2
#: بين السقف وثلاثة أضعافه تُضمَّن الصور **مصغّرة** بدل ربطها: على تشغيل
#: حقيقي (128 لقطة، 16.3MB) خرجت صفحة HTML لا تعمل إن أُرسلت وحدها.
#: التصغير إلى 1280px بجودة 70 يقارب ثلث الحجم ويبقي نصّ الواجهات مقروءًا.
COMPACT_EMBED_FACTOR = 3
COMPACT_MAX_WIDTH = 1280
COMPACT_QUALITY = 70

_CSS = """
:root{
  --bg:#f6f7fb; --card:#ffffff; --ink:#15161c; --muted:#5b6070;
  --line:#e3e5ee; --brand:#2D2E82; --brand-soft:#ececf7; --mark:#ffe9a8;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#14151c; --card:#1c1e28; --ink:#eceef6; --muted:#9aa0b4;
    --line:#2b2e3c; --brand:#8f90e8; --brand-soft:#23253a; --mark:#5a4a12;
  }
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--bg); color:var(--ink); direction:rtl;
  font-family:"Segoe UI","Tahoma","Noto Naskh Arabic","Arial",sans-serif;
  font-size:16px; line-height:1.9;
}
a{color:var(--brand)}
.wrap{display:grid; grid-template-columns:300px minmax(0,1fr); gap:24px;
  max-width:1280px; margin:0 auto; padding:0 20px 80px}
aside{position:sticky; top:0; align-self:start; max-height:100vh;
  overflow:auto; padding:20px 0}
main{min-width:0; padding:20px 0}

header.masthead{background:var(--brand); color:#fff; padding:28px 20px; margin-bottom:8px}
header.masthead .inner{max-width:1280px; margin:0 auto}
header.masthead h1{margin:0 0 6px; font-size:1.7rem; line-height:1.5}
header.masthead .sub{opacity:.85; font-size:.95rem}
.facts{display:flex; flex-wrap:wrap; gap:8px 18px; margin-top:14px; font-size:.85rem; opacity:.9}

.card{background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:18px 20px; margin-bottom:18px}
.card h2{margin:0 0 10px; font-size:1.2rem; color:var(--brand)}
.card h3{margin:18px 0 6px; font-size:1rem; color:var(--muted); font-weight:600}

#search{width:100%; padding:11px 14px; border:1px solid var(--line);
  border-radius:10px; background:var(--card); color:var(--ink); font:inherit; font-size:.95rem}
#searchNote{font-size:.8rem; color:var(--muted); margin:8px 2px 14px; min-height:1.2em}

nav ol{list-style:none; margin:0; padding:0; counter-reset:sec}
nav li{margin:2px 0}
nav a{display:block; padding:7px 10px; border-radius:8px; text-decoration:none;
  color:var(--ink); font-size:.9rem; border-inline-start:3px solid transparent}
nav a:hover{background:var(--brand-soft)}
nav a.active{background:var(--brand-soft); border-inline-start-color:var(--brand); font-weight:600}
nav .t{color:var(--muted); font-size:.78rem; margin-inline-start:6px;
  direction:ltr; display:inline-block; unicode-bidi:isolate}

section.chapter{scroll-margin-top:16px}
section.chapter > h2{
  font-size:1.25rem; color:var(--brand); margin:0 0 4px;
  border-bottom:2px solid var(--brand-soft); padding-bottom:8px}
.summary{color:var(--muted); font-size:.93rem; margin:0 0 14px}
p.para{margin:0 0 14px; text-align:justify}
ul.bullets{margin:0 0 14px; padding-inline-start:22px}
.step{display:flex; gap:12px; align-items:flex-start; margin:0 0 12px}
.step .n{flex:none; width:28px; height:28px; border-radius:50%;
  background:var(--brand); color:#fff; font-weight:700; font-size:.9rem;
  display:flex; align-items:center; justify-content:center}
.step p{margin:3px 0 0}
.note{margin:0 0 14px; padding:10px 14px; border-inline-start:4px solid var(--brand);
  background:var(--brand-soft); border-radius:0 8px 8px 0; font-size:.95rem}
blockquote{margin:0 0 14px; padding:10px 16px; border-inline-start:3px solid var(--brand);
  background:var(--brand-soft); border-radius:0 8px 8px 0}

figure{margin:0 0 20px; background:var(--card); border:1px solid var(--line);
  border-radius:12px; overflow:hidden}
figure img{display:block; width:100%; height:auto; background:#000}
figcaption{padding:10px 14px; font-size:.88rem; color:var(--muted);
  border-top:1px solid var(--line)}
details.ocr{padding:0 14px 10px; font-size:.85rem}
details.ocr summary{cursor:pointer; color:var(--brand)}
details.ocr pre{white-space:pre-wrap; background:var(--bg); padding:10px;
  border-radius:8px; margin:8px 0 0; font-size:.82rem; direction:rtl}

.ts{display:inline-block; direction:ltr; unicode-bidi:isolate;
  font-variant-numeric:tabular-nums; font-size:.8rem; padding:2px 8px;
  border-radius:999px; background:var(--brand-soft); color:var(--brand);
  border:1px solid transparent; cursor:pointer; font-family:inherit}
.ts:hover{border-color:var(--brand)}

#player{position:sticky; top:0; z-index:5; background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:12px; margin-bottom:18px}
#player video{width:100%; border-radius:8px; display:block; background:#000}
#player .hint{font-size:.84rem; color:var(--muted); margin:0 0 8px}
#pickWrap input{font:inherit; font-size:.85rem}

mark{background:var(--mark); color:inherit; border-radius:3px; padding:0 2px}
.hidden{display:none !important}
.empty{color:var(--muted); font-style:italic}

@media (max-width:900px){
  .wrap{grid-template-columns:1fr; padding:0 14px 60px}
  aside{position:static; max-height:none; padding-top:8px}
}
@media print{
  aside,#player,#searchWrap,.ts{display:none !important}
  body{background:#fff; font-size:12pt}
  .wrap{display:block; max-width:none; padding:0}
  .card,figure{border-color:#ccc; break-inside:avoid}
  section.chapter{break-before:page}
  section.chapter:first-of-type{break-before:auto}
  figure{break-inside:avoid}
  a{text-decoration:none; color:#000}
}
"""

_JS = """
(function(){
  var video=null, pending=null;
  var pick=document.getElementById('pick');
  if(pick){
    pick.addEventListener('change',function(){
      var f=pick.files && pick.files[0];
      if(!f) return;
      var el=document.getElementById('vid');
      el.src=URL.createObjectURL(f);
      el.classList.remove('hidden');
      document.getElementById('pickWrap').classList.add('hidden');
      video=el;
      if(pending!==null){ seek(pending); pending=null; }
    });
  }
  function seek(t){
    if(!video){ pending=t; alert('اختر ملف الفيديو أولًا من أعلى الصفحة.'); return; }
    video.currentTime=t; video.play().catch(function(){});
    video.scrollIntoView({block:'nearest'});
  }
  document.addEventListener('click',function(e){
    var b=e.target.closest('.ts'); if(!b) return;
    seek(parseFloat(b.getAttribute('data-t')||'0'));
  });

  // ---- البحث: يُخفي الأقسام التي لا تطابق ويُبرز المطابقات ----------
  var box=document.getElementById('search');
  var note=document.getElementById('searchNote');
  var chapters=[].slice.call(document.querySelectorAll('section.chapter'));
  var links={}; [].forEach.call(document.querySelectorAll('nav a'),function(a){
    links[a.getAttribute('href').slice(1)]=a; });

  // النصّ الأصلي محفوظ مرّة واحدة: الإبراز يعيد كتابة innerHTML، وبلا
  // نسخة أصلية يصير البحث الثاني إبرازًا فوق إبراز حتى تتلف العُقدة.
  var marks=[].slice.call(document.querySelectorAll('[data-searchable]'));
  marks.forEach(function(n){ n.setAttribute('data-orig', n.textContent); });

  function esc(s){ return s.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&'); }
  function clear(){
    marks.forEach(function(n){ n.textContent = n.getAttribute('data-orig'); });
    chapters.forEach(function(c){ c.classList.remove('hidden'); });
    for(var k in links) links[k].classList.remove('hidden');
    note.textContent='';
  }
  var timer=null;
  if(box) box.addEventListener('input',function(){
    clearTimeout(timer); timer=setTimeout(run,140);
  });
  function run(){
    var q=box.value.trim();
    if(q.length<2){ clear(); return; }
    var re=new RegExp(esc(q),'gi'), hits=0;
    chapters.forEach(function(ch){
      var found=0;
      [].forEach.call(ch.querySelectorAll('[data-searchable]'),function(n){
        var orig=n.getAttribute('data-orig');
        if(orig.toLowerCase().indexOf(q.toLowerCase())===-1){
          n.textContent=orig; return;
        }
        found++;
        var out='', last=0, m;
        re.lastIndex=0;
        while((m=re.exec(orig))!==null){
          out+=escapeHtml(orig.slice(last,m.index))+'<mark>'+escapeHtml(m[0])+'</mark>';
          last=m.index+m[0].length;
          if(m.index===re.lastIndex) re.lastIndex++;
        }
        out+=escapeHtml(orig.slice(last));
        n.innerHTML=out;
      });
      hits+=found;
      ch.classList.toggle('hidden',found===0);
      var a=links[ch.id]; if(a) a.classList.toggle('hidden',found===0);
    });
    note.textContent = hits ? ('مطابقات في '+hits+' فقرة') : 'لا نتائج.';
  }
  function escapeHtml(s){
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ---- إبراز القسم الحالي في الفهرس -------------------------------
  if('IntersectionObserver' in window){
    var obs=new IntersectionObserver(function(entries){
      entries.forEach(function(en){
        var a=links[en.target.id]; if(!a) return;
        if(en.isIntersecting){
          for(var k in links) links[k].classList.remove('active');
          a.classList.add('active');
        }
      });
    },{rootMargin:'-10% 0px -75% 0px'});
    chapters.forEach(function(c){ obs.observe(c); });
  }
})();
"""


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _compact_bytes(path: Path) -> bytes:
    import io

    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.width > COMPACT_MAX_WIDTH:
            ratio = COMPACT_MAX_WIDTH / image.width
            image = image.resize((COMPACT_MAX_WIDTH, max(1, int(image.height * ratio))))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=COMPACT_QUALITY, optimize=True)
    return buffer.getvalue()


def _data_uri(path: Path, compact: bool = False) -> Optional[str]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    try:
        if compact:
            data, mime = _compact_bytes(path), "image/jpeg"
        else:
            data = path.read_bytes()
        payload = base64.b64encode(data).decode("ascii")
    except Exception as exc:
        logger.warning(f"تعذّر تضمين الصورة {path.name}: {exc}")
        return None
    return f"data:{mime};base64,{payload}"


def _total_image_bytes(plan: DocumentPlan, images_dir: Path) -> int:
    total = 0
    for section in plan.sections:
        for block in section.blocks:
            if block.kind == "figure" and block.image_filename:
                candidate = images_dir / block.image_filename
                if candidate.is_file():
                    total += candidate.stat().st_size
    return total


def _stamp_button(timestamp: Optional[float]) -> str:
    """زرّ توقيت. خارج الطباعة يقفز بالمشغّل؛ داخلها يختفي كليًّا."""
    if timestamp is None:
        return ""
    return (f'<button class="ts" data-t="{float(timestamp):.3f}" '
            f'title="اقفز إلى هذه اللحظة في الفيديو">'
            f'{_esc(seconds_to_display(timestamp))}</button>')


def _render_block(block: DocumentBlock, images_dir: Path,
                  embed: bool, step_number: int = 0) -> str:
    if block.kind == "paragraph":
        text = (block.text or "").strip()
        if not text:
            return ""
        return f'<p class="para" data-searchable>{_esc(text)}</p>'

    if block.kind == "bullets":
        items = [b for b in (block.bullets or []) if (b or "").strip()]
        if not items:
            return ""
        lines = "".join(f"<li data-searchable>{_esc(i)}</li>" for i in items)
        return f'<ul class="bullets">{lines}</ul>'

    if block.kind == "quote":
        text = (block.text or "").strip()
        if not text:
            return ""
        return f'<blockquote data-searchable>{_esc(text)}</blockquote>'

    if block.kind == "figure":
        return _render_figure(block, images_dir, embed)

    if block.kind == "step":
        text = (block.text or "").strip()
        if not text:
            return ""
        return (f'<div class="step"><span class="n">{step_number}</span>'
                f'<p data-searchable>{_esc(text)}</p></div>')

    if block.kind == "note":
        text = (block.text or "").strip()
        if not text:
            return ""
        return (f'<div class="note" data-searchable><strong>ملاحظة: </strong>'
                f'{_esc(text)}</div>')

    # نوعٌ لا يعرفه المُصيِّر يُعرض فقرةً: لا يضيع نصّ.
    text = (block.text or "").strip()
    return f'<p class="para" data-searchable>{_esc(text)}</p>' if text else ""


def _render_figure(block: DocumentBlock, images_dir: Path,
                   embed: bool) -> str:
    if not block.image_filename:
        return ""
    path = images_dir / block.image_filename
    if not path.is_file():
        # صورة مفقودة لا تُنتج ``<img>`` مكسورًا: الشكل يُسقَط بصمت
        # ويبقى نصّ المستند كاملًا — وهذا هو اتجاه ADR-010 نفسه.
        logger.warning(f"صورة مفقودة، تُخطّت في HTML: {block.image_filename}")
        return ""

    if callable(embed):
        # مُصيِّر آخر (مشغّل SCORM) يقرّر مسار الصورة في حزمته
        src = embed(path)
    else:
        src = (_data_uri(path, compact=embed == "compact") if embed
               else f"keyframes/{block.image_filename}")
    if src is None:
        return ""

    caption = (block.caption or "").strip()
    # النصّ البديل ليس التسمية: التسمية قد تكون فارغة، والبديل لا يكون
    # فارغًا أبدًا (WCAG 1.1.1) — انظر ``document/alt_text``.
    alt = describe(block, block.image_id)
    parts = [f'<figure><img loading="lazy" src="{src}" alt="{_esc(alt)}">']
    if caption or block.timestamp is not None:
        parts.append('<figcaption><span data-searchable>'
                     f'{_esc(caption)}</span> {_stamp_button(block.timestamp)}'
                     '</figcaption>')
    ocr = (block.ocr_text or "").strip()
    if ocr:
        # نصّ الشاشة قابل للبحث لكنه مطويّ: هو مادة بحثٍ لا مادة قراءة،
        # وبسطُه يُغرق الصفحة بنصٍّ آليٍّ متقطّع.
        parts.append('<details class="ocr"><summary>نصّ الشاشة</summary>'
                     f'<pre data-searchable>{_esc(ocr)}</pre></details>')
    parts.append("</figure>")
    return "".join(parts)


def _render_nav(plan: DocumentPlan) -> str:
    items: List[str] = []
    for index, section in enumerate(plan.sections, start=1):
        stamp = ("" if section.start_timestamp is None
                 else f'<span class="t">{_esc(seconds_to_display(section.start_timestamp))}</span>')
        items.append(f'<li><a href="#sec{index}">{_esc(section.title)}{stamp}</a></li>')
    return "<nav><ol>" + "".join(items) + "</ol></nav>"


def _render_facts(ctx: ExportContext) -> str:
    meta = ctx.metadata
    # ما يحتاجه القارئ لا ما يحتاجه المطوّر: الأبعاد والترميز وعدد
    # الأقسام بيانات تشخيص، لا تعريفٌ بالمستند.
    facts = [f"المدة: {seconds_to_display(meta.duration_seconds)}"]
    return "".join(f'<span>{_esc(f)}</span>' for f in facts)


def render_html(ctx: ExportContext) -> str:
    """يبني الصفحة كاملةً كنصّ — منفصلة عن الكتابة ليسهُل اختبارها."""
    plan = ctx.plan
    size = _total_image_bytes(plan, ctx.images_dir)
    embed: object = size <= MAX_EMBEDDED_IMAGE_BYTES
    if not embed and size <= MAX_EMBEDDED_IMAGE_BYTES * COMPACT_EMBED_FACTOR:
        embed = "compact"
        logger.info(
            "صور المهمة %.1f ميغابايت — تُضمَّن مصغّرةً لتبقى الصفحة ملفًّا واحدًا.",
            size / 1024 ** 2)
    elif not embed:
        logger.info(
            "صور المهمة %.1f ميغابايت — تتجاوز سقف التضمين، "
            "فتُربط الصفحة بمجلد keyframes نسبيًّا.", size / 1024 ** 2)

    body: List[str] = []
    for index, section in enumerate(plan.sections, start=1):
        rendered: List[str] = []
        step = 0
        for block in section.blocks:
            if block.kind == "step":
                step += 1
            rendered.append(_render_block(block, ctx.images_dir, embed, step))
        blocks = "".join(rendered)
        summary = (section.summary or "").strip()
        body.append(
            f'<section class="chapter" id="sec{index}">'
            f'<h2 data-searchable>{_esc(section.title)}</h2>'
            + (f'<p class="summary" data-searchable>{_esc(summary)}</p>'
               if summary else "")
            + (blocks or '<p class="empty">— لا محتوى في هذا القسم —</p>')
            + "</section>")

    intro: List[str] = []
    abstract = (plan.abstract or "").strip()
    points = [p for p in (plan.key_points or []) if (p or "").strip()]
    if abstract or points:
        intro.append('<div class="card"><h2>الملخّص</h2>')
        if abstract:
            intro.append(f'<p class="para" data-searchable>{_esc(abstract)}</p>')
        if points:
            intro.append("<h3>النقاط المفتاحية</h3><ul class=\"bullets\">"
                         + "".join(f'<li data-searchable>{_esc(p)}</li>'
                                   for p in points) + "</ul>")
        intro.append("</div>")

    title = _esc(plan.title or ctx.base_path.stem)
    subtitle = (plan.subtitle or "").strip()

    return (
        "<!doctype html>\n"
        '<html lang="ar" dir="rtl">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        '<header class="masthead"><div class="inner">'
        f"<h1>{title}</h1>"
        + (f'<div class="sub">{_esc(subtitle)}</div>' if subtitle else "")
        + f'<div class="facts">{_render_facts(ctx)}</div>'
        "</div></header>\n"
        '<div class="wrap">\n'
        '<aside>'
        '<div id="searchWrap">'
        '<input id="search" type="search" placeholder="ابحث في المحتوى…" '
        'autocomplete="off" aria-label="بحث">'
        '<div id="searchNote" role="status"></div></div>'
        + _render_nav(plan) +
        "</aside>\n<main>\n"
        '<div id="player"><p class="hint">اربط التسجيل بالصفحة ليصير كل '
        'توقيت نقطة قفز — الملف لا يُنسخ ولا يُرفع، ويبقى على جهازك.</p>'
        '<div id="pickWrap"><input id="pick" type="file" accept="video/*,audio/*"></div>'
        '<video id="vid" class="hidden" controls preload="metadata"></video></div>\n'
        + "".join(intro) + "".join(body) +
        "\n</main>\n</div>\n"
        f"<script>{_JS}</script>\n</body>\n</html>\n")


def export(ctx: ExportContext) -> Optional[Path]:
    if not ctx.plan.sections:
        logger.warning("خطة بلا أقسام — تُخطّى صفحة HTML.")
        return None
    path = ctx.sibling(".html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(ctx), encoding="utf-8")
    return path


register("html", export)
