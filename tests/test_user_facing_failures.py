"""ما يراه المعلّم حين يفشل شيء — وما ينجو من OneDrive.

هذان العطبان لا يظهران في أي مقياس جودة ولا في أي بوابة قبول، ومع ذلك
هما أشيع ما سيصادفه زميلٌ يستعمل البرنامج:

1. يفتح المستند في Word، ثم يعيد التشغيل، فيرى نصًّا إنجليزيًّا خامًا.
2. يعمل داخل مجلد يديره OneDrive، فتضيع كل مرحلة مخزَّنة بلا سبب ظاهر.
"""
from __future__ import annotations

import errno
import shutil

import pytest

from core.exceptions import MediaValidationError
from utils.error_reporting import describe_os_error, format_error_for_user
from utils.fingerprints import source_fingerprint

# ── 1 · أخطاء نظام التشغيل ────────────────────────────────────────────


def _win(exc: OSError, code: int) -> OSError:
    """يُلحق رمز ويندوز باستثناء — لتشغيل الاختبار على أي نظام."""
    exc.winerror = code
    return exc


def test_file_open_in_word_gets_an_actionable_arabic_message():
    """‏WinError 32: أشيع فشل متوقّع في البرنامج كلّه."""
    exc = _win(PermissionError(13, "being used by another process",
                               r"C:\Users\IBM\Desktop\lecture.docx"), 32)
    message = format_error_for_user(exc)

    assert "Word" in message
    assert "lecture.docx" in message
    assert "WinError" not in message
    assert "process" not in message


def test_the_message_names_the_file_not_the_whole_path():
    """المستخدم يريد اسم الملف؛ والمسار الكامل في السجلّ."""
    exc = _win(PermissionError(13, "denied",
                               r"C:\Users\IBM\OneDrive\Desktop\محاضرة.docx"), 5)
    message = format_error_for_user(exc)
    assert "محاضرة.docx" in message
    assert "OneDrive" not in message


@pytest.mark.parametrize("code,expected", [
    (errno.ENOSPC, "مساحة"),
    (errno.ENAMETOOLONG, "طويل"),
    (errno.EROFS, "للقراءة فقط"),
    (errno.EACCES, "أغلقه"),
])
def test_known_posix_errors_are_translated(code, expected):
    assert expected in (describe_os_error(OSError(code, "x", "/o/f.docx")) or "")


def test_unknown_errors_keep_their_original_text():
    """رسالة عامة تُخفي كل شيء أسوأ من نصّ إنجليزي — للمستخدم وللتشخيص."""
    exc = OSError(errno.EPIPE, "Broken pipe")
    assert describe_os_error(exc) is None
    assert "Broken pipe" in format_error_for_user(exc)


def test_application_errors_still_win():
    """أخطاء التطبيق المصنَّفة لها رسائلها؛ لا يعترضها مترجم النظام."""
    message = format_error_for_user(MediaValidationError("الملف غير مدعوم"))
    assert message.startswith("[E3001]")


# ── 2 · هوية المهمّة داخل OneDrive ────────────────────────────────────

@pytest.fixture()
def video(tmp_path):
    path = tmp_path / "محاضرة.mp4"
    path.write_bytes(b"\x00\x01\x02" * 5000)
    return path


def test_moving_the_folder_keeps_the_job_identity(video, tmp_path):
    """إعادة توجيه المجلد إلى OneDrive تغيّر المسار المطلق وحده.

    قبل الإصدار 3 كان ذلك يغيّر البصمة، فيتغيّر مجلد المهمّة، فتُهمَل
    كل مرحلة مخزَّنة — ومحاضرة ثلاث ساعات تُفرَّغ من الصفر.
    """
    before = source_fingerprint(video)

    redirected = tmp_path / "OneDrive" / "Desktop"
    redirected.mkdir(parents=True)
    moved = redirected / video.name
    shutil.move(str(video), str(moved))

    assert source_fingerprint(moved) == before


def test_onedrive_rehydration_keeps_the_job_identity(video):
    """‏Files On-Demand تُخلي الملف ثم تُنزله، فيتغيّر mtime وحده."""
    before = source_fingerprint(video)

    import os
    stat = video.stat()
    os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 9_000_000_000))

    assert source_fingerprint(video) == before


def test_changed_content_still_changes_the_identity(video):
    """التساهل له حدّ: محتوى مختلف يبقى مصدرًا مختلفًا."""
    before = source_fingerprint(video)
    video.write_bytes(video.read_bytes() + b"\xff")
    assert source_fingerprint(video) != before


def test_a_different_container_is_a_different_source(tmp_path):
    """اسم الملف باقٍ في البصمة كي يفترق mp4 عن mkv."""
    payload = b"\x00\x01\x02" * 5000
    mp4 = tmp_path / "Lecture.mp4"
    mkv = tmp_path / "Lecture.mkv"
    mp4.write_bytes(payload)
    mkv.write_bytes(payload)
    assert source_fingerprint(mp4) != source_fingerprint(mkv)


def test_a_missing_file_still_yields_a_stable_identity(tmp_path):
    """المسار المفقود يحتاج مجلد مهمّة ثابتًا ليُبلَّغ الخطأ فيه."""
    missing = tmp_path / "لا-يوجد.mp4"
    assert source_fingerprint(missing) == source_fingerprint(missing)
