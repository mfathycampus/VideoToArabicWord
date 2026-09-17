"""أيّ إعداد تفريغ يلتقط الكلام كلّه؟ — يقيس التغطية على تسجيلٍ حقيقي.

    python tools/sweep_transcription.py --media "متابعة تحضير المعلمين.mp4"
    python tools/sweep_transcription.py --media درس.mp4 --only current no_hallucination_skip

سبب وجوده: تسجيل «متابعة تحضير المعلمين» (5:12) خرج مفرَّغًا **24٪** من
مدّته، مع فجوة 89 ثانية (3:13 ← 4:42) فيها كلامٌ فعلي. ``loudnorm`` في
1.11.0 لم يغيّر الرقم (24.7٪ ← 24.2٪)، فالسبب في مكان آخر. المشتبهات:

* ``hallucination_silence_threshold=2.0`` — يتخطّى نوافذ كاملة قرب صمت.
* ``vad_threshold=0.5`` — عالٍ لميكروفون بعيد.
* ``beam_size=1`` من ملفّ المحتوى — الجشع أضعف على العامية.

الأداة تشغّل الإعداد الحالي ثم بدائل تغيّر **عاملًا واحدًا** كلٌّ منها،
على نفس الصوت ونفس المحرّك المحمَّل، وتطبع التغطية وكلمات/دقيقة وأطول
فجوة والزمن. لا تقول أيّ نصٍّ أصحّ — تقول أيّها **لم يُسقط** الكلام.
والنصوص تُحفظ كاملةً في ملفّ JSON لمقارنتها بالعين.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.profiles import apply_profile  # noqa: E402
from config.settings import AppConfig, default_config_path  # noqa: E402

#: كل بديل يغيّر عاملًا واحدًا عن الإعداد الحالي — إلا الأخير، الذي
#: يجمع الإصلاحات المرشَّحة ليُعرف أثرها مجتمعة.
#: مصطلحات الشاشة (hotwords): تُحقن في كل نافذة تفريغ. التشغيل الكامل
#: في الواجهة يمسحها (34 مصطلحًا) والأداة لا — ففرق الزمن بينهما مجهول
#: المصدر. هذه البدائل تقيسه، وتُفعَّل بـ ``--screen-glossary``.
GLOSSARY_VARIANTS = ("hotwords_all", "hotwords_12", "hotwords_none")

#: محرّكات ونماذج بديلة — ``--compare-models``.
MODEL_VARIANTS = {
    "model_large_v3": {"model_size": "large-v3"},
    "engine_cohere": {"__engine__": "cohere-arabic"},
}

VARIANTS: dict[str, dict] = {
    "current": {},
    "no_gap_refill": {"gap_refill": False},
    "no_hallucination_skip": {"hallucination_silence_threshold": None},
    "vad_threshold_0.35": {"vad_threshold": 0.35},
    "vad_off": {"vad_filter": False},
    "beam_5": {"beam_size": 5},
    "combined": {"hallucination_silence_threshold": None,
                 "vad_threshold": 0.35, "beam_size": 5},
}


def _load_config(path: Path | None) -> AppConfig:
    base = AppConfig.load(path or default_config_path())
    return apply_profile(base, base.application.content_profile)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--only", nargs="+",
                        choices=list(VARIANTS) + list(GLOSSARY_VARIANTS)
                        + list(MODEL_VARIANTS),
                        help="بدائل بعينها بدل الكلّ")
    parser.add_argument("--screen-glossary", action="store_true",
                        help="امسح مصطلحات الشاشة كالواجهة وقِس أثرها")
    parser.add_argument("--compare-models", action="store_true",
                        help="قارن large-v3-turbo بـ large-v3 ومحرّك Cohere")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    from audio.transcriber import TranscriptionEngine
    from document.quality import completeness
    from utils.ffmpeg_service import FFmpegService

    config = _load_config(args.config)
    ffmpeg = FFmpegService()
    names = list(args.only or VARIANTS)
    if not args.only:
        if args.screen_glossary:
            names = list(GLOSSARY_VARIANTS)
        if args.compare_models:
            names = ["current", *MODEL_VARIANTS]

    workdir = Path(tempfile.mkdtemp(prefix="sweep_"))
    audio = workdir / "audio_16k.wav"
    print("استخراج الصوت (مع توحيد المستوى كما في البرنامج)…", flush=True)
    ffmpeg.extract_audio(args.media, audio,
                         denoise=config.application.denoise_audio,
                         normalize=getattr(config.application,
                                           "normalize_audio", True))
    levels = ffmpeg.audio_levels(args.media)
    if levels:
        print(f"مستوى المصدر: {levels}", flush=True)
    total_seconds = _wav_seconds(audio)

    glossaries: dict[str, str] = {}
    if any(n in GLOSSARY_VARIANTS for n in names):
        from video.screen_terms import as_glossary, scan_video

        print("مسح مصطلحات الشاشة (كما في الواجهة)…", flush=True)
        terms = scan_video(args.media, ffmpeg, total_seconds)
        glossaries = {"hotwords_all": as_glossary(terms),
                      "hotwords_12": as_glossary(terms[:12]),
                      "hotwords_none": ""}
        print(f"  {len(terms)} مصطلحًا: {glossaries['hotwords_all'][:160]}",
              flush=True)

    engine = TranscriptionEngine(config.whisper.model_copy())
    engine.keep_model_loaded = True
    # من المحرّك لا من الإعداد: المحرّك يملأ ``download_root`` عند إنشائه،
    # وبدونه يُبحث عن النموذج في غير موضعه.
    baseline = engine.config.model_copy()

    results: dict = {}
    for name in names:
        overrides = dict(VARIANTS.get(name) or MODEL_VARIANTS.get(name) or {})
        if name in glossaries:
            overrides["glossary"] = glossaries[name]
        engine_name = overrides.pop("__engine__", None)
        if name == "model_large_v3":
            # نموذجٌ آخر = محرّكٌ جديد؛ المحمَّل لا يُعاد استعماله.
            engine = TranscriptionEngine(config.whisper.model_copy())
            engine.keep_model_loaded = True
        engine.config = baseline.model_copy(update=overrides)
        cfg = engine.config
        print(f"\n▶ {name}  model={engine_name or cfg.model_size} "
              f"beam={cfg.beam_size} vad={cfg.vad_filter}/{cfg.vad_threshold} "
              f"hotwords={len((cfg.glossary or '').split('،')) if cfg.glossary else 0}",
              flush=True)
        started = time.monotonic()
        try:
            if engine_name:
                from audio.engines.registry import build_engine

                other = build_engine(engine_name, config=cfg)
                if not other.is_available():
                    raise RuntimeError(f"غير مثبّت: {other.info.install_hint}")
                result = other.transcribe(audio)
            else:
                result = engine.transcribe(audio)
        except Exception as exc:                        # noqa: BLE001
            print(f"  تعذّر: {exc}", flush=True)
            results[name] = {"overrides": overrides, "error": str(exc)[:400],
                             "seconds": 0.0}
            continue
        elapsed = time.monotonic() - started
        info, _warnings = completeness(result.segments, total_seconds)
        results[name] = {"overrides": overrides,
                         "seconds": round(elapsed, 1),
                         "segments": len(result.segments), **info,
                         "text": result.full_text_clean or result.full_text_raw}
        print(f"  تغطية {info.get('transcript_coverage_pct')}٪ · "
              f"{info.get('words_per_minute')} كلمة/دقيقة · "
              f"أطول فجوة {info.get('longest_gap_seconds')} ث · "
              f"{elapsed:.0f} ث", flush=True)

    out = args.out or Path(f"sweep_{args.media.stem}.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print("\n── الخلاصة ──")
    print(f"{'الإعداد':<24}{'تغطية':>8}{'ك/د':>8}{'فجوة':>8}{'زمن':>8}")
    for name, data in results.items():
        if "error" in data:
            print(f"{name:<24}  تعذّر — {data['error'][:80]}")
            continue
        print(f"{name:<24}{data.get('transcript_coverage_pct', 0):>7}٪"
              f"{data.get('words_per_minute', 0):>8}"
              f"{data.get('longest_gap_seconds', 0):>7}ث"
              f"{data['seconds']:>7}ث")
    print(f"\nالنصوص كاملة في: {out}")
    return 0


def _wav_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


if __name__ == "__main__":
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
