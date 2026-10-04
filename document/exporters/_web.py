"""طبقة العرض المشتركة لصفحات الويب (دليل المذاكرة · مشغّل SCORM).

كل صفحة تُسلَّم ملفًّا مستقلًّا بلا مرجع خارجي (انظر مقدّمة ``html_export``)،
فالأنماط تُضمَّن نصًّا لا ملفًّا. وجودها هنا مرّةً واحدة يعني أن الصفحتين
تخرجان بالهوية نفسها وبالوضع الداكن نفسه والطباعة نفسها.
"""
from __future__ import annotations

FONT_STACK = ('"Segoe UI","Noto Sans Arabic","Noto Naskh Arabic","Tahoma",'
              'system-ui,-apple-system,"Arial",sans-serif')

TOKENS = """
:root{
  --bg:#f4f5fb;--card:#ffffff;--ink:#17182b;--muted:#5b6070;--line:#e2e4f0;
  --brand:#2d2e82;--brand-2:#4547b8;--soft:#ecedf8;--navy:#1b1c55;
  --accent:#f2a900;--accent-soft:#fff3d1;--accent-ink:#7a5600;
  --ok:#0f7b4f;--ok-soft:#e3f5ec;--bad:#b3261e;--bad-soft:#fdeceb;
  --shadow:0 1px 2px rgba(23,24,43,.06),0 10px 28px rgba(23,24,43,.07);
  --radius:16px;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#10111d;--card:#1a1c2c;--ink:#eceef8;--muted:#a0a5bd;--line:#2b2e44;
  --brand:#a3a5f2;--brand-2:#8f91ee;--soft:#24274a;--navy:#0c0d24;
  --accent:#f5b72e;--accent-soft:#3a2f10;--accent-ink:#f5d78a;
  --ok:#5ed6a4;--ok-soft:#133126;--bad:#ff8a80;--bad-soft:#3a1d1a;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px rgba(0,0,0,.35);
}}
"""

BASE = """
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);direction:rtl;
  font-family:__FONT__;font-size:16px;line-height:1.85;
  -webkit-font-smoothing:antialiased}
a{color:var(--brand)}
button{font:inherit;color:inherit}
.hidden{display:none!important}
.in{max-width:1080px;margin:0 auto;padding:0 20px}

/* ── الغلاف ─────────────────────────────────────────────── */
.hero{position:relative;overflow:hidden;color:#fff;padding:44px 0 36px;
  background:linear-gradient(135deg,#1b1c55 0%,#2d2e82 58%,#4547b8 100%)}
.hero::before,.hero::after{content:"";position:absolute;border-radius:50%;
  background:rgba(255,255,255,.06);pointer-events:none}
.hero::before{width:420px;height:420px;left:-120px;top:-180px}
.hero::after{width:300px;height:300px;right:-90px;bottom:-190px}
.hero .in{position:relative;z-index:1}
.kicker{display:inline-block;background:var(--accent);color:#2a1d00;
  font-weight:700;font-size:.8rem;padding:2px 14px;border-radius:999px;
  margin-bottom:14px}
.hero h1{margin:0 0 8px;font-size:clamp(1.5rem,3.4vw,2.3rem);line-height:1.45}
.hero .sub{margin:0;opacity:.85;font-size:1rem}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}
.stat{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);
  border-radius:12px;padding:8px 16px;min-width:96px}
.stat b{display:block;font-size:1.35rem;line-height:1.3}
.stat span{font-size:.8rem;opacity:.85}

/* ── الأزرار والشارات ─────────────────────────────────── */
.btn{display:inline-flex;align-items:center;gap:6px;cursor:pointer;
  background:var(--card);color:var(--brand);border:1px solid var(--line);
  border-radius:10px;padding:6px 14px;font-size:.88rem;font-weight:600;
  transition:border-color .15s,background .15s}
.btn:hover{border-color:var(--brand);background:var(--soft)}
.btn.primary{background:var(--brand);color:#fff;border-color:var(--brand)}
.btn.primary:hover{background:var(--brand-2)}
.btn:focus-visible,.ts:focus-visible,a:focus-visible,summary:focus-visible,
.opt:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.tag{font-size:.75rem;color:var(--muted);background:var(--soft);
  border-radius:999px;padding:1px 11px;white-space:nowrap}
.ts{display:inline-block;direction:ltr;unicode-bidi:isolate;cursor:pointer;
  font-variant-numeric:tabular-nums;font-size:.78rem;padding:1px 10px;
  border-radius:999px;background:var(--soft);color:var(--brand);
  border:1px solid transparent;font-family:inherit}
.ts:hover{border-color:var(--brand)}
.tsl{display:inline-block;direction:ltr;unicode-bidi:isolate;
  font-variant-numeric:tabular-nums;font-size:.78rem;color:var(--muted)}

/* ── البطاقات ─────────────────────────────────────────── */
.card{background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);box-shadow:var(--shadow);padding:18px 22px}
h2.sec{display:flex;align-items:center;gap:10px;font-size:1.3rem;
  color:var(--brand);margin:44px 0 16px;scroll-margin-top:72px}
h2.sec::before{content:"";width:6px;height:1.3em;border-radius:3px;
  background:var(--accent)}
h2.sec .count{font-size:.8rem;font-weight:600;color:var(--muted);
  background:var(--soft);border-radius:999px;padding:0 12px}
"""


def base_css() -> str:
    return TOKENS + BASE.replace("__FONT__", FONT_STACK)
