"""تنزيل النموذج يقول أين وصل.

العطب: التنزيل كان نداءً واحدًا محجوبًا. المستخدم يرى سطرًا واحدًا —
«تنزيل النموذج… يُنفَّذ مرة واحدة فقط» — ثم لا شيء لعشر دقائق أو أكثر
على اتصال مدرسة. فيظنّ البرنامج معلّقًا، فيقتله بعد نصف تنزيل، ثم
يعيد الكرّة من الصفر.
"""
from __future__ import annotations

import pytest

from audio.model_manager import ModelManager, _directory_bytes
from core.exceptions import ModelUnavailableError


class _Manager(ModelManager):
    """مدير بتنزيل مزيّف يكتب بايتات فعلية على القرص."""

    def __init__(self, root, chunks, fail=None):
        super().__init__(root)
        self._chunks = chunks
        self._fail = fail

    def _download(self, name):
        for index, size in enumerate(self._chunks):
            target = self.cache_root / f"part{index}.bin.incomplete"
            target.write_bytes(b"\0" * size)
        if self._fail:
            raise self._fail


class _Spec:
    label_ar = "نموذج الاختبار"
    approx_size_gb = 4 / 1024          # 4 ميجابايت


def _run(manager, callback, spec=None):
    manager.cache_root.mkdir(parents=True, exist_ok=True)
    manager._download_with_progress("test", spec or _Spec(), callback,
                                    poll_seconds=0.01)


def test_progress_is_reported_during_the_download(tmp_path):
    mb = 1024 * 1024
    manager = _Manager(tmp_path / "models", [mb, mb, mb, mb])
    seen = []
    _run(manager, seen.append)

    assert seen, "التنزيل مرّ بلا أي إشارة تقدّم"
    assert any("ميجابايت" in line for line in seen)
    assert any("٪" in line for line in seen)


def test_it_never_claims_a_hundred_before_finishing(tmp_path):
    """الحجم المتوقّع تقريبي؛ 100٪ قبل الانتهاء كذبة مزعجة."""
    mb = 1024 * 1024
    manager = _Manager(tmp_path / "models", [mb * 40])   # أكبر بكثير من المتوقّع
    seen = []
    _run(manager, seen.append)

    assert not any("(100٪)" in line for line in seen)


def test_progress_only_moves_forward(tmp_path):
    mb = 1024 * 1024
    manager = _Manager(tmp_path / "models", [mb, mb, mb])
    seen = []
    _run(manager, seen.append)

    percents = [int(line.split("(")[-1].rstrip("٪)")) for line in seen
                if line.endswith("٪)")]
    assert percents == sorted(percents)
    assert len(set(percents)) == len(percents), "تكرّرت النسبة نفسها"


def test_a_failed_download_raises_a_model_error(tmp_path):
    manager = _Manager(tmp_path / "models", [1024],
                       fail=RuntimeError("انقطع الاتصال"))
    with pytest.raises(ModelUnavailableError, match="انقطع الاتصال"):
        _run(manager, lambda _line: None)


def test_no_callback_is_not_an_error(tmp_path):
    """سطر الأوامر قد لا يمرّر خطّافًا."""
    manager = _Manager(tmp_path / "models", [1024])
    _run(manager, None)


def test_directory_bytes_counts_incomplete_parts(tmp_path):
    """‏snapshot_download يكتب ‎.incomplete‎ ثم يعيد التسمية.

    لو أُهملت هذه الملفّات لبقي العدّاد صفرًا طوال التنزيل ثم قفز
    إلى النهاية — أسوأ من غياب التقدّم.
    """
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / "x.bin.incomplete").write_bytes(b"\0" * 2048)
    (tmp_path / "done.bin").write_bytes(b"\0" * 1024)
    assert _directory_bytes(tmp_path) == 3072


def test_directory_bytes_survives_a_vanishing_file(tmp_path):
    """إعادة التسمية تحدث أثناء المسح — الملف يختفي بين الخطوتين."""
    (tmp_path / "a.bin").write_bytes(b"\0" * 10)
    assert _directory_bytes(tmp_path) == 10
    assert _directory_bytes(tmp_path / "لا-يوجد") == 0
