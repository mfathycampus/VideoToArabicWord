# VideoToArabicWord 1.4.0 — Hardened R3

## Runtime hardening

- DOCX is written to a temporary file in the destination directory, validated as a healthy OOXML ZIP, then atomically published.
- Environment Doctor now checks disk space and write access in addition to runtime dependencies and binaries.
- Runtime errors expose stable machine-readable `error_code` and `category` values.
- Missing optional runtime dependencies no longer make engine discovery/import fail; the pipeline still blocks execution with an actionable Arabic message.
- Added `tools/build_smoke.py` for post-PyInstaller bundle validation.

## Verification

Targeted hardening suite: 34 passed.

Full suite in the current sandbox is intentionally not treated as a release gate because the supplied package does not contain `testdata/corpus/manifest.json` and this sandbox does not install `faster-whisper`. Those environment/test-data gaps are reported separately rather than masked.

## Release gate still required on Windows

1. Build with PyInstaller on the target Python version.
2. Run `python tools/build_smoke.py dist/VideoToArabicWord`.
3. Launch the EXE on a clean Windows machine.
4. Run Doctor.
5. Process a short Arabic video.
6. Open and validate the generated DOCX in Microsoft Word.
