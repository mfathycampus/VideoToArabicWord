"""مُدقِّق القبول — يشغّل كل البوابات ويصدر تقريرًا بالأرقام.

الاستخدام:
    python tools/run_acceptance.py [--wer-report wer_large_v3.json]

يعرّف "نسبة العمل" تعريفًا قابلًا للقياس:

    نسبة اجتياز البوابات = البوابات الناجحة ÷ إجمالي البوابات

البوابات ثلاث مجموعات:
    1. اختبارات الوحدات والتكامل (pytest)
    2. مصفوفة التوافق: كل ملف فيديو يُعالَج حتى مستند صالح
    3. بوابات جودة المخرج على كل مستند ناتج
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "testdata" / "corpus"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS = {"w": W[1:-1]}


def run_pytest() -> dict:
    print("\n[1/3] تشغيل حزمة الاختبارات…")
    start = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
         "--tb=no", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=2400)
    tail = result.stdout.strip().splitlines()[-1] if result.stdout else ""
    passed = failed = skipped = 0
    for token in tail.replace(",", " ").split():
        pass
    import re
    for count, label in re.findall(r"(\d+) (passed|failed|skipped|error)", tail):
        if label == "passed":
            passed = int(count)
        elif label == "failed":
            failed = int(count)
        elif label == "skipped":
            skipped = int(count)
    print(f"      {passed} ناجح · {failed} فاشل · {skipped} متخطى "
          f"({time.time() - start:.0f}s)")
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "summary": tail, "seconds": round(time.time() - start, 1)}


def run_matrix() -> dict:
    """يشغّل الـ pipeline على كل ملف في المصفوفة ويجمع النتائج."""
    print("\n[2/3] مصفوفة التوافق مع أنواع الفيديو…")
    from config.settings import AppConfig
    from core.exceptions import AppBaseException
    from core.pipeline import VideoToDocPipeline
    from tests.test_corpus_matrix import StubModelManager, StubTranscriber

    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    out_root = ROOT / "acceptance_out"
    if out_root.exists():
        import shutil
        shutil.rmtree(out_root)

    rows, documents = [], []
    for entry in manifest:
        video = CORPUS / entry["file"]
        if not video.exists():
            rows.append({**entry, "result": "missing", "ok": False})
            continue
        pipeline = VideoToDocPipeline(
            out_root / entry["file"].replace(".", "_"), AppConfig(),
            transcriber=StubTranscriber(), model_manager=StubModelManager())
        expect_failure = entry["category"] == "must_fail"
        start = time.time()
        try:
            docx = pipeline.run(video)
            ok = not expect_failure
            rows.append({**entry, "result": "processed", "ok": ok,
                         "seconds": round(time.time() - start, 2),
                         "docx": str(docx)})
            documents.append(docx)
        except AppBaseException as exc:
            # فشل مُدار برسالة عربية = السلوك الصحيح للملفات التالفة
            arabic = any("؀" <= c <= "ۿ" for c in str(exc))
            rows.append({**entry, "result": "handled_error", "ok": expect_failure and arabic,
                         "error": str(exc)[:200],
                         "seconds": round(time.time() - start, 2)})
        except Exception as exc:
            rows.append({**entry, "result": "crash", "ok": False,
                         "error": f"{type(exc).__name__}: {exc}"[:200]})
        mark = "✓" if rows[-1]["ok"] else "✗"
        print(f"      {mark} {entry['file']:<34s} {rows[-1]['result']}")

    passed = sum(1 for r in rows if r["ok"])
    return {"total": len(rows), "passed": passed, "failed": len(rows) - passed,
            "rows": rows, "documents": [str(d) for d in documents]}


def run_output_gates(documents: list[str]) -> dict:
    """بوابات جودة على كل مستند ناتج فعليًا."""
    print("\n[3/3] بوابات جودة المخرج…")
    from lxml import etree

    gates = {
        "docx_is_valid_zip": 0, "required_parts_present": 0,
        "all_xml_parses": 0, "arabic_runs_have_cs_font": 0,
        "arabic_runs_have_szcs": 0, "arabic_runs_have_rtl": 0,
        "bidi_precedes_jc": 0, "table_uses_bidivisual": 0,
        "section_is_rtl": 0, "has_embedded_images": 0, "has_toc_field": 0,
        "has_headings": 0,
        "page_number_field": 0, "no_dash_separators": 0,
    }
    total = len(documents)
    failures: list[str] = []

    for path_str in documents:
        path = Path(path_str)
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is None:
                    gates["docx_is_valid_zip"] += 1
                names = archive.namelist()
                if all(p in names for p in ("[Content_Types].xml",
                                            "word/document.xml",
                                            "word/_rels/document.xml.rels")):
                    gates["required_parts_present"] += 1
                try:
                    for part in names:
                        if part.endswith(".xml"):
                            etree.fromstring(archive.read(part))
                    gates["all_xml_parses"] += 1
                except Exception:
                    failures.append(f"{path.name}: XML غير صالح")

                root = etree.fromstring(archive.read("word/document.xml"))
                media = [n for n in names if n.startswith("word/media/")]
                media_hashes = [hashlib.sha1(archive.read(n)).hexdigest()
                                for n in media]
                footer = b"".join(archive.read(n) for n in names
                                  if "footer" in n and n.endswith(".xml"))

            cs = szcs = rtl = True
            texts = []
            for run in root.iter(f"{W}r"):
                text = "".join(t.text or "" for t in run.iter(f"{W}t"))
                texts.append(text)
                if not any("؀" <= ch <= "ۿ" for ch in text):
                    continue
                rPr = run.find("w:rPr", NS)
                if rPr is None:
                    cs = szcs = rtl = False
                    continue
                fonts = rPr.find("w:rFonts", NS)
                cs &= bool(fonts is not None and fonts.get(f"{W}cs"))
                szcs &= rPr.find("w:szCs", NS) is not None
                rtl &= rPr.find("w:rtl", NS) is not None
            gates["arabic_runs_have_cs_font"] += cs
            gates["arabic_runs_have_szcs"] += szcs
            gates["arabic_runs_have_rtl"] += rtl
            if not (cs and szcs and rtl):
                failures.append(f"{path.name}: خصائص Complex Script ناقصة")

            ordered = True
            for pPr in root.iter(f"{W}pPr"):
                tags = [c.tag.replace(W, "") for c in pPr]
                if "bidi" in tags and "jc" in tags and \
                        tags.index("bidi") > tags.index("jc"):
                    ordered = False
            gates["bidi_precedes_jc"] += ordered

            tblPr = next(root.iter(f"{W}tblPr"), None)
            gates["table_uses_bidivisual"] += bool(
                tblPr is not None
                and any(c.tag.replace(W, "") == "bidiVisual" for c in tblPr))

            sectPr = next(root.iter(f"{W}sectPr"), None)
            gates["section_is_rtl"] += bool(
                sectPr is not None and sectPr.find("w:bidi", NS) is not None)

            # البوابة الصادقة: صور مضمّنة **متى أُنتجت لقطات**. فيديو
            # منتظم اللون تمامًا لا لقطات له، ومستنده بلا صور سلوك صحيح
            # لا عطل.
            job_dir = path.parent
            keyframes_file = job_dir / "keyframes.json"
            expected = 0
            if keyframes_file.exists():
                expected = len(json.loads(
                    keyframes_file.read_text(encoding="utf-8"))["keyframes"])
            # الشعار وأي أصل من أصول القالب صور مضمّنة أيضًا، فلا
            # تُحسب ضمن اللقطات: نستبعدها بمطابقة البصمة لا بالامتداد.
            branding = {hashlib.sha1(f.read_bytes()).hexdigest()
                        for f in ASSETS.glob("*") if f.is_file()}
            frames = [h for h in media_hashes if h not in branding]
            gates["has_embedded_images"] += (len(frames) == expected)
            body = etree.tostring(root)
            gates["has_toc_field"] += b"TOC" in body
            gates["has_headings"] += b'w:pStyle w:val="Heading' in body \
                or b'Heading1' in body or b"Heading 1" in body
            gates["page_number_field"] += b"PAGE" in footer
            gates["no_dash_separators"] += not any("-----" in t for t in texts)
        except Exception as exc:
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")

    for name, count in gates.items():
        mark = "✓" if count == total else "✗"
        print(f"      {mark} {name:<32s} {count}/{total}")
    return {"total_documents": total, "gates": gates, "failures": failures,
            "checks_passed": sum(gates.values()),
            "checks_total": len(gates) * total}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wer-report", type=Path, default=None)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "acceptance_report.json")
    args = parser.parse_args()

    started = time.time()
    tests = run_pytest()
    matrix = run_matrix()
    gates = run_output_gates(matrix["documents"])

    total_checks = (tests["passed"] + tests["failed"]
                    + matrix["total"] + gates["checks_total"])
    passed_checks = (tests["passed"] + matrix["passed"] + gates["checks_passed"])
    rate = passed_checks / total_checks if total_checks else 0.0

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": round(time.time() - started, 1),
        "tests": tests,
        "compatibility_matrix": {k: v for k, v in matrix.items()
                                 if k != "documents"},
        "output_gates": gates,
        "totals": {"checks_total": total_checks,
                   "checks_passed": passed_checks,
                   "pass_rate": round(rate, 4)},
    }
    if args.wer_report and args.wer_report.exists():
        wer = json.loads(args.wer_report.read_text(encoding="utf-8"))
        report["transcription_accuracy"] = {
            k: wer[k] for k in ("model", "samples", "audio_seconds", "wer",
                                "cer", "word_accuracy", "char_accuracy",
                                "realtime_factor") if k in wer}

    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"  إجمالي الفحوص : {total_checks}")
    print(f"  الناجحة       : {passed_checks}")
    print(f"  نسبة الاجتياز : {rate:.2%}")
    if "transcription_accuracy" in report:
        acc = report["transcription_accuracy"]
        print(f"  دقة التفريغ   : كلمات {acc['word_accuracy']:.2%} · "
              f"حروف {acc['char_accuracy']:.2%}  ({acc['model']})")
    print(f"{'=' * 60}")
    print(f"التقرير: {args.output}")
    return 0 if rate >= 0.97 else 1


if __name__ == "__main__":
    raise SystemExit(main())
