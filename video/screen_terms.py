"""اقتراح مصطلحات التفريغ من نصّ الشاشة.

**سبب وجود هذا الملف مقيس، وحدُّه مقيس أيضًا.**

على تسجيل حقيقي مدّته 88 ثانية، وبتفريغ صحّحه صاحبه بيده، كانت 18 من
38 كلمة خاطئة — نصفُ الأخطاء تقريبًا — **أسماء قوائم إنجليزية**:

    المرجع                      ما أخرجه البرنامج
    Modify Course Schedule      «مضيفه كورس سكيدور»
    Drop All                    «فهي ادل لي»
    Drop Classes                (سقطت)

وخانة «مصطلحات المادة» كانت فارغة. وملؤها باليد رفع استرجاع هذه
المصطلحات من **صفر من 12** إلى **8 من 12**.

والمصطلحات نفسها **مكتوبة على الشاشة في الفيديو**. مسحُ أربعة عشر
إطارًا من التسجيل نفسه أخرج ``Modify Course Schedule`` أربع مرّات
و``Drop Classes`` أربع مرّات — أي أسوأ مصطلحين في المخرَج، آليًّا.

──────────────────────────────────────────────────────────────────────

**ولماذا يقترح هذا الملف ولا يحقن:**

جرّبتُ الحقن التلقائي وقِسْتُه، فتبيّن أنني لا أستطيع إثبات أنه يُحسّن:
تشغيلان **بنفس الإعداد ونفس الصوت** أعطيا WER 38.8٪ و34.7٪. أربع نقاط
فرق من لا شيء، وهي بحجم كل فرق بين الإعدادات. العيّنة (121 كلمة) أصغر
من أن تحسم.

ورأيتُ إشارة خطر واحدة فعلًا: كلّما زادت المصطلحات الإنجليزية مال
النموذج إلى الإنجليزية (1 كلمة لاتينية بلا قاموس ← 10 ← 18)، وفي
تشغيل أخرج مقطعًا بالإنجليزية كاملًا مكان جملة عربية. المصطلح الذي
يظهر على الشاشة **ولا يُنطق** يدفع النموذج بلا مقابل.

فالمكسب مثبت (الاستخراج)، والمخاطرة غير مقيسة (انحراف اللغة). ومن
يفصل بينهما هو المستخدم: يرى المقترَح، ويحذف ما لم يُنطق، ثم يبدأ.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from document.titles import looks_like_browser_chrome, looks_like_url
from utils.logger import logger

#: كم إطارًا نمسح. ثابت لا يتبع طول الفيديو: محاضرة ثلاث ساعات بمسحٍ
#: كل ستّ ثوانٍ تعني 1800 إطارًا، أي عشرات الدقائق من OCR قبل أن يبدأ
#: التفريغ أصلًا. وأسماء القوائم تتكرّر، فالعيّنة تكفي.
MAX_SCAN_FRAMES = 14

#: أقصر عبارة تُقبل. «OK» و«Go» لا تفيد التفريغ ولا تخطئ فيها النماذج.
MIN_TERM_CHARS = 4

#: سقف طول القاموس المقترَح بالحروف. يطابق ``_HOTWORDS_CHAR_LIMIT`` في
#: ``audio/transcriber.py``: ما يتجاوزه يُقتطع هناك، فاقتطاعه هنا —
#: على حدّ عبارة كاملة — أنظف من قصٍّ في منتصف كلمة.
MAX_GLOSSARY_CHARS = 400

#: عبارة إنجليزية: حرف ثم حروف أو شَرْطة. الأرقام مستبعَدة عمدًا —
#: «Section 3» و«2024» ليست مصطلحات بل بيانات صفحة.
_TERM = re.compile(r"[A-Za-z][A-Za-z&/-]{2,}")

#: كلمات واجهة عامّة موجودة في كل برنامج، ولا يخطئ فيها التفريغ لأنها
#: نادرًا ما تُنطق. إبقاؤها يزاحم المصطلحات الحقيقية على سقف الحروف.
_CHROME = {
    "file", "edit", "view", "help", "close", "open", "save", "cancel",
    "home", "search", "menu", "back", "next", "new", "delete", "print",
    "tools", "settings", "options", "window", "page", "click", "select",
    "chrome", "firefox", "mozilla", "edge", "safari", "google",
    "http", "https", "www", "com", "org", "net",
    "the", "and", "for", "all", "of", "in", "to", "on", "at", "by",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
}


def _phrases(line: str) -> List[str]:
    """يقطع السطر إلى عبارات إنجليزية متّصلة.

    العبارة أنفع من الكلمة المفردة: ``Modify Course Schedule`` تُنطق
    ككتلة، والنموذج يخطئ فيها ككتلة.
    """
    found: List[str] = []
    run: List[str] = []
    for token in line.split():
        if _TERM.fullmatch(token):
            run.append(token)
        elif run:
            found.append(" ".join(run))
            run = []
    if run:
        found.append(" ".join(run))
    return found


def terms_from_screen_text(texts: Iterable[str]) -> List[tuple[str, int]]:
    """‏(عبارة، عدد مرّات ظهورها) مرتّبةً بالتكرار ثم بالطول.

    التكرار هو الترتيب الصحيح: ما يظهر على الشاشة مرارًا هو تسمية
    واجهة ثابتة، وما يظهر مرّة قد يكون قراءةً فاشلة أو بيانَ صفحة.
    """
    counter: Counter = Counter()
    for text in texts:
        for line in (text or "").splitlines():
            # سطرٌ كامل يُطرح إن كان شريط متصفّح أو مسارًا. التعريف
            # مستعار من ``document/titles.py`` لا مكرَّر: افتراق
            # التعريفين هناك هو ما أنتج «Googke Chrome» عنوانًا لقسم.
            if looks_like_url(line) or looks_like_browser_chrome(line):
                continue
            for phrase in _phrases(line):
                words = [w for w in phrase.split()
                         if w.lower() not in _CHROME]
                cleaned = " ".join(words)
                if len(cleaned) >= MIN_TERM_CHARS:
                    counter[cleaned] += 1
    ranked = sorted(counter.items(),
                    key=lambda item: (-item[1], -len(item[0])))

    # ما ظهر مرّة واحدة يُستبعَد ما دام هناك بديلٌ متكرّر: القراءة
    # الفاشلة تظهر مرّة، وتسمية الواجهة تتكرّر. على تسجيل حقيقي كان
    # المتكرّر «Drop Classes» و«Current Student Selection»، والمفرد
    # «House» و«Quick Data» — ضوضاء تزاحم المفيد على سقف الحروف.
    repeated = [item for item in ranked if item[1] > 1]
    return repeated if len(repeated) >= 3 else ranked


def as_glossary(terms: Sequence[tuple[str, int]],
                limit: int = MAX_GLOSSARY_CHARS) -> str:
    """يبني نصّ القاموس، ويقف عند السقف على حدّ عبارة كاملة."""
    chosen: List[str] = []
    used = 0
    for phrase, _count in terms:
        addition = len(phrase) + (2 if chosen else 0)
        if used + addition > limit:
            continue
        chosen.append(phrase)
        used += addition
    return "، ".join(chosen)


def scan_video(video_path: Path,
               ffmpeg,
               duration_seconds: float,
               max_frames: int = MAX_SCAN_FRAMES,
               progress: Optional[Callable[[int, int], None]] = None,
               work_dir: Optional[Path] = None) -> List[tuple[str, int]]:
    """يمسح إطارات موزّعة على الفيديو ويُعيد المصطلحات المرشَّحة.

    قائمة فارغة عند غياب Tesseract أو فشل الاستخراج: هذا اقتراحٌ
    مساعد، ولا يجوز أن يمنع فشلُه بدءَ المعالجة.
    """
    from video.ocr import extract_text, is_available

    if not is_available():
        logger.info("مسح المصطلحات: Tesseract غير متوفّر — تُخطّي.")
        return []
    if duration_seconds <= 0:
        return []

    import tempfile

    texts: List[str] = []
    count = max(1, min(max_frames, int(duration_seconds // 2) or 1))

    with tempfile.TemporaryDirectory(prefix="terms_") as tmp:
        base = Path(work_dir or tmp)
        base.mkdir(parents=True, exist_ok=True)
        try:
            frames = ffmpeg.extract_frames_evenly(
                video_path, base, count, duration_seconds, width=1280)
        except Exception as exc:                            # noqa: BLE001
            logger.info(f"مسح المصطلحات: تعذّر استخراج الإطارات: {exc}")
            return []

        for index, image in enumerate(frames):
            if progress:
                progress(index, len(frames))
            try:
                texts.append(extract_text(image))
            except Exception as exc:                        # noqa: BLE001
                # إطارٌ واحد يفشل لا يُسقط المسح كلّه.
                logger.debug(f"مسح المصطلحات: تعذّر الإطار {index}: {exc}")
            finally:
                image.unlink(missing_ok=True)

    if progress:
        progress(count, count)
    terms = terms_from_screen_text(texts)
    logger.info(f"مسح المصطلحات: {len(terms)} مرشّحًا من {count} إطارًا.")
    return terms
