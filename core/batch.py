"""معالجة مجلد كامل: مقرّر بأكمله في تشغيلة واحدة.

الطبقة السفلى كانت جاهزة لهذا منذ البداية — ``JobStatus.QUEUED`` معرّف في
``core/job_state`` ولم يستعمله أحد، وكل مهمة تملك مجلدها وحالتها
واستئنافها. الناقص كان الحلقة التي تمرّ على الملفات.

قاعدة جوهرية: **فشل ملف لا يوقف الدفعة.** من يشغّل عشرين محاضرة ليلًا
يجب أن يجد تسعة عشر مستندًا صباحًا وتقريرًا بالملف الذي فشل، لا صفرًا
ورسالة خطأ.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from config.settings import AppConfig, ClipRange
from core.exceptions import PipelineCancelledError
from utils.cancellation import CancellationToken
from utils.logger import logger

VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
                  ".ts", ".m4v", ".mpg", ".mpeg", ".3gp"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus",
                  ".wma", ".aiff", ".aif", ".amr", ".mka", ".oga", ".3ga",
                  ".caf", ".ac3", ".m4b", ".mp2", ".au"}
MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES

ProgressFn = Callable[[int, int, str, float], None]


@dataclass
class BatchResult:
    source: Path
    ok: bool
    output: Optional[Path] = None
    error: Optional[str] = None
    job_dir: Optional[Path] = None


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


def iter_media(folder: Path, recursive: bool = False) -> List[Path]:
    """يجمع ملفات الوسائط في مجلد، مرتّبة بالاسم لتكون النتيجة متوقّعة."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    walker = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        (p for p in walker
         if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES),
        key=lambda p: p.name)


def process_folder(
    sources: Sequence[Path],
    output_dir: Path,
    config: Optional[AppConfig] = None,
    *,
    allow_model_download: bool = False,
    transcript_only: bool = False,
    clip: Optional[ClipRange] = None,
    cancel_token: Optional[CancellationToken] = None,
    on_progress: Optional[ProgressFn] = None,
    on_file_done: Optional[Callable[[BatchResult], None]] = None,
) -> List[BatchResult]:
    """يعالج الملفات واحدًا تلو الآخر ويعيد نتيجة لكلٍّ منها.

    الإلغاء يوقف الدفعة كلها فورًا؛ أي خطأ آخر يُسجَّل ويُنتقل إلى التالي.
    """
    from core.pipeline import VideoToDocPipeline

    config = config or AppConfig()
    results: List[BatchResult] = []
    total = len(sources)

    # ‏pipeline واحد للدفعة كلها. كان يُبنى واحدٌ لكل ملف، ومعه محرّك
    # تفريغ جديد، فيُعاد تحميل 1.7GB من القرص مع كل ملف: 35 ثانية باردة
    # لكل واحد — دفعةٌ من عشرين ملفًا تهدر ثلاث إلى عشر دقائق في إعادة
    # قراءة الأوزان نفسها. مجلد المهمة يُشتقّ من المصدر لا من الـpipeline
    # (``job_dir_for``)، فلا حالة تتسرّب بين الملفات.
    pipeline = VideoToDocPipeline(output_dir, config)
    # يُبقي الأوزان في الذاكرة بين ملف وآخر — الغرض من مشاركة الـpipeline.
    # ‏getattr لأن المحرّكات تُحقَن (ADR-008): بديلٌ — أو بديل اختبار —
    # قد لا يعرض ``transcriber`` أصلًا، وذلك ليس خطأ يستحقّ إسقاط الدفعة.
    transcriber = getattr(pipeline, "transcriber", None)
    if transcriber is not None:
        transcriber.keep_model_loaded = True

    for index, source in enumerate(sources, start=1):
        if cancel_token and cancel_token.is_cancelled():
            logger.info("أُلغيت الدفعة بطلب المستخدم.")
            break

        logger.info(f"[{index}/{total}] {source.name}")
        job_dir = pipeline.job_dir_for(source, clip)

        def progress(pct: float, _message: str, _name=source.name,
                     _index=index) -> None:
            if on_progress:
                on_progress(_index, total, _name, pct)

        try:
            output = pipeline.run(
                source,
                cancel_token=cancel_token,
                progress_callback=progress,
                allow_model_download=allow_model_download,
                allow_audio_only=is_audio(source),
                clip=clip,
                transcript_only=transcript_only)
            result = BatchResult(source=source, ok=True, output=output,
                                 job_dir=job_dir)
        except PipelineCancelledError:
            logger.info("أُلغيت الدفعة أثناء معالجة ملف.")
            results.append(BatchResult(source=source, ok=False,
                                       error="أُلغيت", job_dir=job_dir))
            break
        except Exception as exc:
            # ملف تالف بين عشرين سليمًا يجب ألا يُضيّع الليلة كلها
            logger.warning(f"فشل {source.name}: {type(exc).__name__}: {exc}")
            result = BatchResult(source=source, ok=False,
                                 error=f"{type(exc).__name__}: {exc}",
                                 job_dir=job_dir)

        results.append(result)
        if on_file_done:
            on_file_done(result)

    return results
