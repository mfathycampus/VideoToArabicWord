"""قياس دقة التفريغ على تسجيلاتك الحقيقية — لا على عيّنة عامة.

الرقم المنشور في README (92.54%) مقيس على 86 ثانية من FLEURS بلهجة مصرية:
كلام مقروء في استوديو. ما يهمّ المدرسة والجامعة دقّته على **حصّة فعلية**:
لهجة خليجية، مصطلحات إنجليزية، ميكروفون حاسوب، أكثر من متكلّم. هذه الأداة
تبني ذلك القياس من تسجيلاتك في ثلاث خطوات:

    1) python tools/eval_real.py prepare "C:\\Users\\…\\Maeen Recordings"
          يقتطع ~12 دقيقة من مقاطع قصيرة موزّعة على كل التسجيلات، ويفرّغها
          بالنموذج، ويكتب صفحة مراجعة: <المجلد>/_eval/review.html

    2) افتح review.html: اسمع كل مقطع وصحّح نصّه، ثم «تنزيل المراجع».

    3) python tools/eval_real.py score "C:\\Users\\…\\Maeen Recordings\\_eval"
          يقارن ما كتبه النموذج بما صحّحته ويطبع الدقة بمجال ثقتها.

**الخصوصية:** كل شيء على جهازك. المقاطع والنصوص تُكتب في ``_eval`` بجوار
التسجيلات — خارج المستودع، والأداة ترفض الكتابة داخله: في التسجيلات أصوات
آخرين.

**حدّ منهجي يُكتب مع كل نتيجة:** المرجع يُبنى بتصحيح مسودّة النموذج نفسه،
فالعين تميل إلى قبول ما كُتب («انحياز الإرساء») والرقم يخرج **متفائلًا**
قليلًا. ``prepare --blank`` يترك الخانات فارغة لقياس أشدّ صرامة بثمن وقتٍ
أطول في الكتابة.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MEDIA_SUFFIXES = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".m4v", ".m4a", ".mp3",
                  ".wav", ".flac", ".opus", ".ogg", ".wma", ".aac"}
EVAL_DIR_NAME = "_eval"

#: ثوانٍ تُتخطّى من أول التسجيل وآخره: تحيّات وضبط ميكروفون وصمت الختام.
EDGE_SECONDS = 20.0
#: أقل نسبة كلام ليُقبل المقطع — دونها يُجرَّب موضع آخر في الشريحة نفسها.
MIN_SPEECH_RATIO = 0.5
SPEECH_RETRIES = 4


@dataclass(frozen=True)
class Clip:
    name: str
    source: str
    start: float
    duration: float


# ---------------------------------------------------------------------
# اختيار المقاطع — عشوائي طبقي ببذرة ثابتة
# ---------------------------------------------------------------------
def plan_clips(durations: Dict[str, float], total_seconds: float,
               clip_seconds: float, seed: int = 2026) -> List[Clip]:
    """يوزّع المقاطع على التسجيلات بنسبة أطوالها، وداخل كل تسجيل بالتساوي.

    كل تسجيل يُقسَّم شرائحَ متساوية بعدد مقاطعه، ومن كل شريحة مقطع بموضع
    عشوائي — فلا تتركّز العيّنة في أول الحصّة ولا يتقاطع مقطعان. البذرة
    الثابتة تجعل إعادة التشغيل تختار المقاطع نفسها.
    """
    spans = {}
    for name, duration in sorted(durations.items()):
        edge = EDGE_SECONDS if duration >= clip_seconds + 4 * EDGE_SECONDS else 0.0
        usable = duration - 2 * edge
        if usable >= clip_seconds:
            spans[name] = (edge, usable)
    if not spans:
        return []

    wanted = max(1, round(total_seconds / clip_seconds))
    capacity = {name: int(usable // clip_seconds) for name, (_, usable) in spans.items()}
    wanted = min(wanted, sum(capacity.values()))
    total_usable = sum(usable for _, usable in spans.values())

    # حصّة كل تسجيل: واحد على الأقل إن اتّسع العدد، ثم الباقي بأكبر الكسور.
    counts = {name: 0 for name in spans}
    if wanted >= len(spans):
        for name in spans:
            counts[name] = 1
    remaining = wanted - sum(counts.values())
    shares = {name: remaining * usable / total_usable
              for name, (_, usable) in spans.items()}
    for name in spans:
        add = min(int(shares[name]), capacity[name] - counts[name])
        counts[name] += add
    leftover = wanted - sum(counts.values())
    order = sorted(spans, key=lambda n: (shares[n] - int(shares[n]), spans[n][1]),
                   reverse=True)
    while leftover > 0:
        progressed = False
        for name in order:
            if leftover and counts[name] < capacity[name]:
                counts[name] += 1
                leftover -= 1
                progressed = True
        if not progressed:
            break

    rng = random.Random(seed)
    clips: List[Clip] = []
    for name in sorted(spans):
        offset, usable = spans[name]
        count = counts[name]
        if not count:
            continue
        stratum = usable / count
        for index in range(count):
            slack = stratum - clip_seconds
            start = offset + index * stratum + rng.uniform(0, max(0.0, slack))
            clips.append(Clip("", name, round(start, 2), float(clip_seconds)))
    return [Clip(f"clip_{i:02d}", c.source, c.start, c.duration)
            for i, c in enumerate(clips, start=1)]


def stratum_bounds(clip: Clip, durations: Dict[str, float],
                   siblings: int, index: int) -> tuple:
    """حدود الشريحة التي ينتمي إليها المقطع — لإعادة السحب داخلها."""
    duration = durations[clip.source]
    edge = EDGE_SECONDS if duration >= clip.duration + 4 * EDGE_SECONDS else 0.0
    usable = duration - 2 * edge
    stratum = usable / siblings
    low = edge + index * stratum
    return low, max(low, low + stratum - clip.duration)


def prefer_speech(clips: List[Clip], durations: Dict[str, float],
                  speech_ratio: Callable[[str, float, float], float],
                  seed: int = 2026) -> tuple:
    """يستبدل المقطع الصامت بموضع آخر **من شريحته**. يعيد (المقاطع، الضعيفة).

    مقطعٌ كله صمت أو انتظار لا يقيس شيئًا. لكن الاستبدال داخل الشريحة نفسها
    فقط — وإلا صار الاختيار «أين الصوت أوضح» لا عيّنةً من الحصّة.
    """
    rng = random.Random(seed + 1)
    per_source: Dict[str, List[int]] = {}
    for position, clip in enumerate(clips):
        per_source.setdefault(clip.source, []).append(position)

    result, weak = list(clips), []
    for source, positions in per_source.items():
        for index, position in enumerate(positions):
            clip = result[position]
            best, best_ratio = clip, speech_ratio(clip.source, clip.start, clip.duration)
            low, high = stratum_bounds(clip, durations, len(positions), index)
            tries = 0
            while best_ratio < MIN_SPEECH_RATIO and tries < SPEECH_RETRIES and high > low:
                tries += 1
                start = round(rng.uniform(low, high), 2)
                ratio = speech_ratio(source, start, clip.duration)
                if ratio > best_ratio:
                    best, best_ratio = Clip(clip.name, source, start, clip.duration), ratio
            result[position] = best
            if best_ratio < MIN_SPEECH_RATIO:
                weak.append(best.name)
    return result, weak


# ---------------------------------------------------------------------
# ffmpeg: المدد، نسبة الكلام، الاقتطاع
# ---------------------------------------------------------------------
def media_files(folder: Path) -> List[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)


def probe_duration(path: Path) -> float:
    from utils.media_probe import probe_raw

    data = probe_raw(path)
    try:
        return float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


_SILENCE = re.compile(r"silence_duration:\s*([0-9.]+)")


def measure_speech_ratio(ffmpeg, path: Path, start: float, duration: float) -> float:
    """نسبة ما ليس صمتًا في النافذة (``silencedetect`` — بلا أي نموذج)."""
    result = ffmpeg._run([
        "-ss", f"{start:.2f}", "-t", f"{duration:.2f}", "-i", str(path),
        "-vn", "-map", "0:a:0", "-af", "silencedetect=noise=-35dB:d=0.4",
        "-f", "null", "-"], timeout=120, loglevel="info")
    silent = sum(float(m) for m in _SILENCE.findall(result.stderr or ""))
    return max(0.0, min(1.0, 1.0 - silent / duration)) if duration else 0.0


# ---------------------------------------------------------------------
# صفحة المراجعة
# ---------------------------------------------------------------------
def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


REVIEW_TEMPLATE = """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>مراجعة مقاطع القياس — معين</title>
<style>
body{font-family:"Segoe UI",Tahoma,system-ui,sans-serif;background:#f6f7f6;color:#12211f;
     max-width:860px;margin:24px auto;padding:0 16px;line-height:1.8}
h1{font-size:22px;margin:0 0 6px}.sub{color:#5d6b68;margin:0 0 16px}
.card{background:#fff;border:1px solid #dfe5e3;border-radius:12px;padding:14px 16px;margin:0 0 14px}
.card.done{border-color:#1c7c54}.card.skip{opacity:.55}
.head{display:flex;justify-content:space-between;gap:12px;font-size:13px;color:#5d6b68}
audio{width:100%;margin:8px 0}
textarea{width:100%;min-height:96px;font:inherit;font-size:17px;padding:10px;border:1px solid #dfe5e3;
         border-radius:8px;box-sizing:border-box;direction:rtl}
label{font-size:14px;color:#5d6b68}
.rules{background:#eef5f2;border-radius:10px;padding:12px 18px;margin:0 0 18px}
.rules li{margin:2px 0}
.bar{position:sticky;top:0;background:#f6f7f6;padding:10px 0;display:flex;gap:12px;align-items:center;
     border-bottom:1px solid #dfe5e3;margin-bottom:14px;z-index:2}
button{background:#0b2e2a;color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;cursor:pointer}
#count{color:#5d6b68;font-size:14px}
</style></head><body>
<h1>مراجعة مقاطع القياس</h1>
<p class="sub">__SUMMARY__</p>
<ul class="rules">
<li><b>اكتب ما قيل كما قيل.</b> بالعامية إن قيل بالعامية. لا تُصلح لغة المتكلّم ولا تُكمل ما لم يُنطق.</li>
<li>__DRAFT_RULE__</li>
<li>الكلمة الإنجليزية والرقم: إن كانت المسودّة كتبتهما صحيحين فاتركهما بصيغتهما (PowerSchool تبقى PowerSchool، و25 تبقى 25).</li>
<li>لا تهتمّ بالتشكيل ولا بعلامات الترقيم ولا بالهمزات — القياس يتجاهلها.</li>
<li>مقطع بلا كلام واضح، أو أصوات متداخلة لا تُفهم: علّم «استبعد هذا المقطع».</li>
<li>تقدّمك يُحفظ في هذا المتصفّح تلقائيًّا. حين تنتهي اضغط «تنزيل المراجع» وضع الملف في هذا المجلد.</li>
</ul>
<div class="bar"><button onclick="download()">تنزيل المراجع</button><span id="count"></span></div>
<div id="cards"></div>
<script>
const DATA = __DATA__;
const KEY = "maeen-eval-" + DATA.eval_id;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (err) { state = {}; }

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (err) {}
  let done = 0;
  for (const clip of DATA.clips) {
    const entry = state[clip.name] || {};
    const card = document.getElementById("card-" + clip.name);
    card.classList.toggle("done", Boolean(entry.reviewed) && !entry.exclude);
    card.classList.toggle("skip", Boolean(entry.exclude));
    if (entry.reviewed || entry.exclude) done += 1;
  }
  document.getElementById("count").textContent =
    "رُوجع " + done + " من " + DATA.clips.length;
}

function build() {
  const host = document.getElementById("cards");
  for (const clip of DATA.clips) {
    const entry = state[clip.name] || (state[clip.name] = {text: clip.draft, exclude: false, reviewed: false});
    const card = document.createElement("div");
    card.className = "card"; card.id = "card-" + clip.name;

    const head = document.createElement("div"); head.className = "head";
    const title = document.createElement("span"); title.textContent = clip.name + " — " + clip.where;
    const skipLabel = document.createElement("label");
    const skip = document.createElement("input"); skip.type = "checkbox"; skip.checked = Boolean(entry.exclude);
    skip.addEventListener("change", () => { entry.exclude = skip.checked; save(); });
    skipLabel.append(skip, " استبعد هذا المقطع");
    head.append(title, skipLabel);

    const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none";
    audio.src = "clips/" + clip.name + ".wav";

    const area = document.createElement("textarea"); area.value = entry.text; area.lang = "ar";
    area.addEventListener("input", () => { entry.text = area.value; entry.reviewed = true; save(); });
    const okLabel = document.createElement("label");
    const ok = document.createElement("input"); ok.type = "checkbox"; ok.checked = Boolean(entry.reviewed);
    ok.addEventListener("change", () => { entry.reviewed = ok.checked; save(); });
    okLabel.append(ok, " استمعتُ وراجعتُ هذا النص");

    card.append(head, audio, area, okLabel);
    host.append(card);
  }
  save();
}

function download() {
  const pending = DATA.clips.filter((c) => !(state[c.name].reviewed || state[c.name].exclude)).length;
  if (pending && !confirm("بقي " + pending + " مقطعًا لم يُراجَع. غير المراجَع يُستبعد من القياس. تنزيل الآن؟")) return;
  const out = {eval_id: DATA.eval_id, saved_at: new Date().toISOString(), clips: state};
  const blob = new Blob([JSON.stringify(out, null, 2)], {type: "application/json"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob); link.download = "references.json"; link.click();
}
build();
</script></body></html>
"""


def render_review(eval_id: str, clips: List[dict], blank: bool) -> str:
    """صفحة واحدة بلا أي اعتماد خارجي. البيانات تُحقن JSON لا HTML."""
    minutes = sum(c["duration"] for c in clips) / 60
    summary = (f"{len(clips)} مقطعًا · {minutes:.0f} دقيقة تقريبًا · "
               f"قدّر للمراجعة {max(10, round(minutes * 4))} دقيقة.")
    rule = ("الخانات فارغة عمدًا: اكتب النص من السماع." if blank else
            "النص المكتوب مسودّة النموذج: <b>صحّح أخطاءها</b> — كلمة ناقصة، زائدة، "
            "أو مسموعة خطأً — ولا تُعِد صياغة الصحيح.")
    payload = {"eval_id": eval_id, "clips": [
        {"name": c["name"], "draft": "" if blank else c.get("hyp", ""),
         "where": f"{c['source']} @ {_clock(c['start'])}"} for c in clips]}
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return (REVIEW_TEMPLATE.replace("__SUMMARY__", html.escape(summary))
            .replace("__DRAFT_RULE__", rule).replace("__DATA__", data))


# ---------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------
def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def prepare(folder: Path, out: Optional[Path] = None, minutes: float = 12.0,
            clip_seconds: float = 30.0, seed: int = 2026,
            model: Optional[str] = None, blank: bool = False,
            ffmpeg=None, durations: Optional[Dict[str, float]] = None,
            transcribe: Optional[Callable[[Path], str]] = None,
            log: Callable[[str], None] = print) -> Path:
    out = Path(out) if out else folder / EVAL_DIR_NAME
    if inside_repo(out):
        raise SystemExit(
            "مجلد القياس داخل المستودع. المقاطع فيها أصوات آخرين ولا تدخل Git — "
            "اختر مجلدًا خارجه (الافتراضي: _eval بجوار التسجيلات).")
    if ffmpeg is None:
        from utils.ffmpeg_service import FFmpegService
        ffmpeg = FFmpegService()

    files = {p.name: p for p in media_files(folder)}
    if not files:
        raise SystemExit(f"لا ملفات وسائط في: {folder}")
    if durations is None:
        durations = {name: probe_duration(path) for name, path in files.items()}
    hours = sum(durations.values()) / 3600
    log(f"التسجيلات: {len(files)} · {hours:.1f} ساعة")

    clips = plan_clips(durations, minutes * 60, clip_seconds, seed)
    if not clips:
        raise SystemExit("كل التسجيلات أقصر من طول المقطع المطلوب.")
    clips, weak = prefer_speech(
        clips, durations,
        lambda source, start, length: measure_speech_ratio(
            ffmpeg, files[source], start, length), seed)
    log(f"المقاطع: {len(clips)} × {clip_seconds:.0f} ث"
        + (f" · {len(weak)} منها قليل الكلام (راجعها أو استبعدها)" if weak else ""))

    clip_dir = out / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    if transcribe is None and not blank:
        transcribe = _engine_transcriber(model)
    model_name = model or _default_model()

    records = []
    for index, clip in enumerate(clips, start=1):
        wav = clip_dir / f"{clip.name}.wav"
        # الاستخراج نفسه الذي يجريه البرنامج (تسوية المستوى مفعّلة): المقيس
        # هو ما يسمعه النموذج في الإنتاج، لا صوتٌ أُعدّ للقياس.
        ffmpeg.extract_audio(files[clip.source], wav, start_seconds=clip.start,
                             end_seconds=clip.start + clip.duration)
        record = {**asdict(clip), "file": str(wav), "hyp": "", "model": "",
                  "weak_speech": clip.name in weak}
        if not blank:
            started = time.time()
            record["hyp"] = transcribe(wav)
            record["model"] = model_name
            record["seconds"] = round(time.time() - started, 1)
        records.append(record)
        log(f"  [{index}/{len(clips)}] {clip.name}  {clip.source} @ {_clock(clip.start)}")

    eval_id = f"{seed}-{len(records)}-{int(sum(durations.values()))}"
    manifest = {"eval_id": eval_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source_folder": str(folder), "seed": seed, "blank": blank,
                "model": "" if blank else model_name, "clips": records}
    (out / "eval_set.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "review.html").write_text(
        render_review(eval_id, records, blank), encoding="utf-8")
    log(f"\nجاهز. افتح للمراجعة:\n  {out / 'review.html'}")
    return out


def _default_model() -> str:
    from audio.model_manager import DEFAULT_MODEL
    return DEFAULT_MODEL


def _engine_transcriber(model: Optional[str]) -> Callable[[Path], str]:
    """محرّك الإنتاج بإعدادات المصنع — النموذج يُحمَّل مرّة واحدة."""
    from audio.transcriber import TranscriptionEngine
    from config.settings import TranscriptionConfig

    engine = TranscriptionEngine(TranscriptionConfig(model_size=model or _default_model()))
    engine.keep_model_loaded = True

    def run(path: Path) -> str:
        result = engine.transcribe(path)
        return result.full_text_clean or result.full_text_raw

    return run


# ---------------------------------------------------------------------
# score
# ---------------------------------------------------------------------
def find_references(eval_dir: Path, explicit: Optional[Path] = None) -> Path:
    if explicit:
        return Path(explicit)
    candidates = list(eval_dir.glob("references*.json"))
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        candidates += list(downloads.glob("references*.json"))
    if not candidates:
        raise SystemExit(
            "لم أجد references.json. اضغط «تنزيل المراجع» في review.html وضع "
            f"الملف في:\n  {eval_dir}\nأو مرّر مساره بـ --references.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _pooled(rows: List[dict], key: str) -> float:
    errors = sum(row[key][0] for row in rows)
    length = sum(row[key][1] for row in rows)
    return errors / length if length else 0.0


def bootstrap_interval(rows: List[dict], key: str, rounds: int = 2000,
                       seed: int = 2026) -> tuple:
    """مجال ثقة 95% بإعادة سحب **المقاطع** — المقطع وحدة العيّنة لا الكلمة."""
    if len(rows) < 2:
        value = _pooled(rows, key)
        return value, value
    rng = random.Random(seed)
    values = sorted(_pooled([rng.choice(rows) for _ in rows], key)
                    for _ in range(rounds))
    return values[int(0.025 * rounds)], values[int(0.975 * rounds) - 1]


def score(manifest: dict, references: dict,
          hypotheses: Optional[Dict[str, str]] = None) -> dict:
    """يقارن نصّ النموذج المحفوظ (أو ``hypotheses``) بالمرجع المصحَّح."""
    from tools.measure_wer import cer, wer, wer_tolerant

    if references.get("eval_id") not in (None, manifest.get("eval_id")):
        raise SystemExit("ملف المراجع يخصّ مجموعة قياس أخرى (eval_id مختلف).")
    entries = references.get("clips") or {}
    rows, excluded, unreviewed = [], [], []
    for clip in manifest["clips"]:
        entry = entries.get(clip["name"]) or {}
        if entry.get("exclude"):
            excluded.append(clip["name"])
            continue
        reference = (entry.get("text") or "").strip()
        if not entry.get("reviewed") or not reference:
            unreviewed.append(clip["name"])
            continue
        hypothesis = (hypotheses or {}).get(clip["name"], clip.get("hyp", ""))
        w, we, wl = wer(reference, hypothesis)
        c, ce, cl = cer(reference, hypothesis)
        t, te, tl = wer_tolerant(reference, hypothesis)
        rows.append({"name": clip["name"], "source": clip["source"],
                     "duration": clip["duration"], "wer": (we, wl), "cer": (ce, cl),
                     "tol": (te, tl), "untouched": we == 0,
                     "weak_speech": bool(clip.get("weak_speech"))})
    if not rows:
        raise SystemExit("لا مقاطع مراجَعة بعد — راجع المقاطع في review.html أولًا.")

    low, high = bootstrap_interval(rows, "wer")
    by_source = {}
    for row in rows:
        by_source.setdefault(row["source"], []).append(row)
    words = sum(row["wer"][1] for row in rows)
    report = {
        "eval_id": manifest.get("eval_id"),
        "model": manifest.get("model") or "?",
        "clips_scored": len(rows), "clips_excluded": len(excluded),
        "clips_unreviewed": len(unreviewed),
        "audio_minutes": round(sum(row["duration"] for row in rows) / 60, 1),
        "reference_words": words,
        "wer": round(_pooled(rows, "wer"), 4),
        "wer_ci95": [round(low, 4), round(high, 4)],
        "cer": round(_pooled(rows, "cer"), 4),
        "wer_orthography_tolerant": round(_pooled(rows, "tol"), 4),
        "word_accuracy": round(1 - _pooled(rows, "wer"), 4),
        "word_accuracy_ci95": [round(1 - high, 4), round(1 - low, 4)],
        "char_accuracy": round(1 - _pooled(rows, "cer"), 4),
        "untouched_clips": sum(row["untouched"] for row in rows),
        "reference_from_draft": not manifest.get("blank", False),
        "by_source": {source: {"clips": len(items),
                               "wer": round(_pooled(items, "wer"), 4)}
                      for source, items in sorted(by_source.items())},
        "worst_clips": [
            {"name": row["name"], "wer": round(row["wer"][0] / row["wer"][1], 4)}
            for row in sorted(rows, key=lambda r: r["wer"][0] / max(1, r["wer"][1]),
                              reverse=True)[:5] if row["wer"][1]],
        "caveats": [],
    }
    if report["reference_from_draft"]:
        report["caveats"].append(
            "المرجع بُني بتصحيح مسودّة النموذج نفسه — الرقم متفائل قليلًا "
            "(انحياز الإرساء). للقياس الأشدّ: prepare --blank.")
    if words < 800:
        report["caveats"].append(
            f"العيّنة صغيرة ({words} كلمة): مجال الثقة واسع. راجع مقاطع أكثر.")
    if report["untouched_clips"] > 0.8 * len(rows) and len(rows) >= 5:
        report["caveats"].append(
            "أكثر من 80% من المقاطع لم يُغيَّر فيها حرف — تأكّد أنها سُمعت فعلًا.")
    return report


def print_report(report: dict, log: Callable[[str], None] = print) -> None:
    low, high = report["word_accuracy_ci95"]
    log(f"\nالنموذج: {report['model']}")
    log(f"المقاطع المقيسة: {report['clips_scored']}  "
        f"({report['audio_minutes']} دقيقة · {report['reference_words']} كلمة)"
        f" · مستبعدة {report['clips_excluded']} · غير مراجَعة {report['clips_unreviewed']}")
    log("=" * 56)
    log(f"دقة الكلمات : {report['word_accuracy']:.1%}   "
        f"(مجال الثقة 95%: {low:.1%} – {high:.1%})")
    log(f"دقة الحروف  : {report['char_accuracy']:.1%}")
    log(f"دقة الكلمات بتسامح إملائي: {1 - report['wer_orthography_tolerant']:.1%}")
    if len(report["by_source"]) > 1:
        log("\nلكل تسجيل:")
        for source, item in report["by_source"].items():
            log(f"  {1 - item['wer']:6.1%}  ({item['clips']} مقطعًا)  {source}")
    for caveat in report["caveats"]:
        log(f"\n⚠ {caveat}")


def run_score(eval_dir: Path, references: Optional[Path] = None,
              model: Optional[str] = None, log: Callable[[str], None] = print) -> dict:
    manifest = json.loads((eval_dir / "eval_set.json").read_text(encoding="utf-8"))
    refs_path = find_references(eval_dir, references)
    refs = json.loads(refs_path.read_text(encoding="utf-8"))
    log(f"المراجع: {refs_path}")
    hypotheses = None
    if model and model != manifest.get("model"):
        # مقارنة نموذج آخر على المراجع نفسها: يُعاد التفريغ ولا تُمسّ المسودّة.
        transcribe = _engine_transcriber(model)
        reviewed = {name for name, entry in (refs.get("clips") or {}).items()
                    if entry.get("reviewed") and not entry.get("exclude")}
        hypotheses = {}
        for clip in manifest["clips"]:
            if clip["name"] in reviewed:
                hypotheses[clip["name"]] = transcribe(eval_dir / "clips" / f"{clip['name']}.wav")
        manifest = {**manifest, "model": model}
    report = score(manifest, refs, hypotheses)
    suffix = "" if hypotheses is None else f"_{re.sub(r'[^A-Za-z0-9.-]+', '-', model or '')}"
    (eval_dir / f"report{suffix}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report, log)
    log(f"\nالتقرير: {eval_dir / f'report{suffix}.json'}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="قياس دقة التفريغ على تسجيلاتك الحقيقية")
    commands = parser.add_subparsers(dest="command", required=True)

    prep = commands.add_parser("prepare", help="اقتطاع المقاطع وتفريغها وبناء صفحة المراجعة")
    prep.add_argument("folder", type=Path, help="مجلد التسجيلات")
    prep.add_argument("--out", type=Path, help="مجلد القياس (الافتراضي: <المجلد>/_eval)")
    prep.add_argument("--minutes", type=float, default=12.0, help="مجموع دقائق العيّنة")
    prep.add_argument("--clip-seconds", type=float, default=30.0)
    prep.add_argument("--seed", type=int, default=2026)
    prep.add_argument("--model", help="نموذج التفريغ (الافتراضي: نموذج البرنامج)")
    prep.add_argument("--blank", action="store_true",
                      help="بلا مسودّة: اكتب المرجع من السماع (أدقّ وأطول)")

    scr = commands.add_parser("score", help="حساب الدقة بعد المراجعة")
    scr.add_argument("eval_dir", type=Path, help="مجلد القياس (_eval)")
    scr.add_argument("--references", type=Path, help="مسار references.json")
    scr.add_argument("--model", help="أعد التفريغ بنموذج آخر وقارنه على المراجع نفسها")

    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args.folder, args.out, args.minutes, args.clip_seconds, args.seed,
                args.model, args.blank)
    else:
        run_score(args.eval_dir, args.references, args.model)
    return 0


if __name__ == "__main__":
    from utils.console import enable_utf8_console
    enable_utf8_console()
    raise SystemExit(main())
