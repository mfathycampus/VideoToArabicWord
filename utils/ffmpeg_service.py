"""واجهة موحّدة لكل استدعاءات FFmpeg — لا يُستدعى ffmpeg خارج هذه الوحدة."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from core.exceptions import FFmpegExecutionError, MediaValidationError
from utils.logger import logger
from utils.media_probe import _CREATE_NO_WINDOW, resolve_ffmpeg


class FFmpegService:
    def __init__(self) -> None:
        self._exe: Optional[str] = None

    @property
    def executable(self) -> str:
        if self._exe is None:
            self._exe = resolve_ffmpeg()
        return self._exe

    def _run(self, args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
        cmd = [self.executable, "-y", "-hide_banner", "-loglevel", "error", *args]
        logger.debug(f"ffmpeg: {' '.join(cmd[:8])} …")
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
                creationflags=_CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegExecutionError(
                f"تجاوز FFmpeg المهلة ({timeout}s).") from exc

    #: توحيد المستوى. **مُطبَّق افتراضيًّا** — وهذا أهمّ سطر في الملفّ.
    #:
    #: كان جزءًا من سلسلة ``--denoise`` المعطّلة افتراضيًّا، والتعليق
    #: فوقه يقول إن تذبذب المستوى «يُربك VAD فيبتلع مقاطع كاملة». ثم
    #: وقع ذلك بالضبط على تسجيل حقيقي، وقِيس:
    #:
    #:   تسجيل شاشة 5:12 · وسيط ‎-36.5 dBFS · ذروة ‎-26.6
    #:   المفرَّغ: 77 ثانية من 312 = **24.7٪**
    #:   والمقطع 193–283s — 58 ثانية منه كلامٌ فعليّ — خرج **فارغًا**
    #:   و``loudnorm`` يرفع ذلك المقطع من ‎-36.0 إلى ‎-20.7 dBFS
    #:
    #: أي أن علاج أشدّ أعطال البرنامج كان مكتوبًا وموصوفًا ومعطَّلًا.
    #: التطبيع رخيص (مرور واحد على الصوت المستخرَج أصلًا) ولا يُتلف
    #: صوتًا سليمًا — فلا سبب لجعله اختيارًا.
    LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"

    #: خفض الضوضاء وحده — يبقى اختياريًّا. ``afftdn`` عدوانيّ على
    #: أصوات هادئة أو مسجَّلة بميكروفون ضعيف، فتشغيله للجميع يُتلف
    #: صوتًا سليمًا ليُصلح صوتًا رديئًا.
    DENOISE_FILTER = "highpass=f=80,afftdn=nf=-25"

    #: تحت هذه الذروة يُعدّ التسجيل هادئًا ويُسجَّل تنبيه. الرقم من
    #: القياس أعلاه: ذروة ‎-26.6 dBFS كانت تُنتج تفريغًا ربعَ كامل.
    QUIET_PEAK_DBFS = -20.0

    def audio_levels(self, media_path: Path,
                     timeout: int = 180) -> Optional[dict]:
        """يقيس ذروة الصوت ومتوسّطه بـ``volumedetect`` — بلا فكّ كامل.

        يُستعمل للتشخيص لا للقرار: التطبيع يُطبَّق دائمًا، وهذا يقول
        للمستخدم **لماذا** كان تفريغه ضعيفًا قبل الإصلاح.
        """
        result = self._run([
            "-i", str(media_path), "-vn", "-map", "0:a:0",
            "-af", "volumedetect", "-f", "null", "-"], timeout=timeout)
        levels: dict = {}
        for key, field in (("max_volume", "peak_db"),
                           ("mean_volume", "mean_db")):
            match = re.search(rf"{key}:\s*(-?\d+(?:\.\d+)?) dB",
                              result.stderr or "")
            if match:
                levels[field] = float(match.group(1))
        return levels or None

    def extract_audio(self, video_path: Path, output_path: Path,
                      sample_rate: int = 16000,
                      start_seconds: float = 0.0,
                      end_seconds: Optional[float] = None,
                      denoise: bool = False,
                      normalize: bool = True) -> Path:
        """يستخرج صوتًا أحاديًا 16kHz PCM — الصيغة التي يتوقعها Whisper.

        ``-ss`` قبل ``-i`` للبحث السريع (يقفز بلا فكّ ترميز)، و ``-to``
        بعده لأنه يُحسب من نقطة البدء.

        ``normalize`` مُفعَّل افتراضيًّا — انظر ``LOUDNORM_FILTER``.
        ``denoise`` يبقى اختياريًّا ويُضاف **قبل** التطبيع: التنظيف ثم
        التسوية، لا العكس؛ تطبيعٌ يسبق خفض الضوضاء يرفع الضوضاء معه.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        if start_seconds > 0:
            args += ["-ss", f"{start_seconds:.3f}"]
        args += ["-i", str(video_path)]
        if end_seconds is not None:
            args += ["-to", f"{max(0.0, end_seconds - start_seconds):.3f}"]
        args += [
            "-vn",                       # تجاهل الفيديو
            "-map", "0:a:0",             # أول مسار صوتي فقط
        ]
        chain = []
        if denoise:
            chain.append(self.DENOISE_FILTER)
        if normalize:
            chain.append(self.LOUDNORM_FILTER)
        if chain:
            args += ["-af", ",".join(chain)]
        args += [
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",                  # أحادي
            str(output_path),
        ]
        result = self._run(args)
        if result.returncode != 0 or not output_path.exists():
            raise FFmpegExecutionError(
                f"فشل استخراج الصوت: {result.stderr.strip()[:400]}")
        if output_path.stat().st_size == 0:
            raise FFmpegExecutionError("الملف الصوتي الناتج فارغ.")
        return output_path

    def extract_frame(self, video_path: Path, timestamp: float,
                      output_path: Path, width: Optional[int] = None) -> Path:
        """يستخرج إطارًا واحدًا عند زمن محدد.

        يحترم بيانات الدوران تلقائيًا (بعكس OpenCV)، ويُستخدم كمسار
        احتياطي عندما يعجز OpenCV عن فك ترميز معيّن.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filters = ["scale=%d:-2" % width] if width else []
        args = ["-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video_path),
                "-frames:v", "1"]
        if filters:
            args += ["-vf", ",".join(filters)]
        args += ["-q:v", "2", str(output_path)]
        result = self._run(args, timeout=120)
        if result.returncode != 0 or not output_path.exists():
            raise FFmpegExecutionError(
                f"فشل استخراج الإطار عند {timestamp:.2f}s: "
                f"{result.stderr.strip()[:300]}")
        return output_path

    def extract_frames_evenly(self, video_path: Path, output_dir: Path,
                              count: int, duration_seconds: float,
                              width: Optional[int] = None) -> list[Path]:
        """يستخرج ``count`` إطارًا موزّعة على الفيديو في **تمريرة واحدة**.

        بديلٌ عن نداء ``extract_frame`` لكل إطار، ولذلك سببان:

        * **الدقّة.** ``-ss`` قبل ``-i`` بحثٌ سريع يقف عند أقرب إطار
          مفتاحي **قبل** الزمن المطلوب، وقد يبعد ثوانيَ في تسجيل قليل
          الإطارات المفتاحية. ومرشّح ``fps`` يأخذ من التدفّق المفكوك،
          فيقع حيث يُفترض. قياس: المسح بالبحث السريع فوّت قائمةً ظهرت
          في التسجيل أربع مرّات، والتقطها المرشّح.
        * **الزمن.** نداء واحد يفتح الملف مرّة بدل ``count`` مرّة.

        يُعيد المسارات الموجودة فعلًا: ‏ffmpeg قد يُخرج إطارًا أقلّ أو
        أكثر بواحد حسب التقريب، وهذا لا يُعدّ فشلًا.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        if count <= 0 or duration_seconds <= 0:
            return []

        filters = [f"fps={count}/{duration_seconds:.3f}"]
        if width:
            filters.append(f"scale={width}:-2")
        pattern = output_dir / "scan_%04d.jpg"
        result = self._run(["-i", str(video_path), "-vf", ",".join(filters),
                            "-q:v", "3", "-frames:v", str(count + 2),
                            str(pattern)], timeout=600)
        frames = sorted(output_dir.glob("scan_*.jpg"))
        if result.returncode != 0 and not frames:
            raise FFmpegExecutionError(
                f"فشل مسح الإطارات: {result.stderr.strip()[:300]}")
        return frames

    def validate_readable(self, media_path: Path,
                          expect_video: bool = True) -> None:
        """يتحقق من إمكانية فك ترميز المصدر فعليًا قبل بدء الـ pipeline.

        ‏``-frames:v 1`` يفشل حتمًا على ملف صوتي سليم، فلا يصلح فحصًا
        موحّدًا. لمصدر صوتي نفكّ عيّنة من الصوت بدل الإطار.
        """
        args = (["-i", str(media_path), "-frames:v", "1", "-f", "null", "-"]
                if expect_video else
                ["-i", str(media_path), "-t", "1", "-f", "null", "-"])
        result = self._run(args, timeout=120)
        if result.returncode != 0:
            kind = "الفيديو" if expect_video else "الملف الصوتي"
            raise MediaValidationError(
                f"تعذر فك ترميز {kind}: {result.stderr.strip()[:300]}")
