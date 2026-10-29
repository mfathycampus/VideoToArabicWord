"""1.18.0 — ما يتجاوز الأسماء: شاشات الدخول وأرقام الهوية والروابط والمسارات،
ورسالة الموافقة التي كانت تقول إن الصور لا تُرسل.

الحالات من تسجيلٍ حقيقي (1.15.1، 49 دقيقة عن Schoology وChalk وTeams).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from video.sensitive import (classify_screen, find_sensitive_boxes,
                             is_identifier, scrub_screen_text)


def _w(text, left, line=(1, 1, 1), top=300, width=None, height=14):
    return {"text": text, "left": float(left), "top": float(top),
            "width": float(width or 8 * len(text)), "height": float(height),
            "line": line}


# ── شاشات الدخول ─────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Microsoft\nSign in\nUse passkey from another device\nManage passwords...",
    "maarif\nStay signed in?\nDo this to reduce the number of times you are asked",
    "Welcome to Microsoft 365\nSign in with your Microsoft account to get started",
    "Sign in with a passkey\nScan this QR code with your phone",
    "تسجيل الدخول\nالبريد الإلكتروني\nكلمة المرور",
])
def test_login_screens_seen_in_the_real_run_are_detected(text):
    assert classify_screen(text) == ["login"]


@pytest.mark.parametrize("text", [
    "Schoology\nCourses Groups Resources\nRecent Activity\nSign in",  # زرّ في الرأس
    "Create a New Semester\nSEMESTER NAME\n2026-2027 School Year",
    "Copy Lesson to\nCurriculum & Instruction Lessons",
    "",
])
def test_ordinary_pages_are_not_login_screens(text):
    assert classify_screen(text) == []


# ── أرقام الهوية: مرساة صفّ الطالب ───────────────────────────────────
def test_identifier_needs_nine_mostly_digit_characters():
    assert is_identifier("(13111819712")
    assert is_identifier("1600001893")
    assert not is_identifier("6046704")          # رقم شعبة
    assert not is_identifier("26/10/18")
    assert not is_identifier("maarif.schoology.com/course/8510020295")


def test_student_row_is_blurred_around_its_id_even_when_the_name_is_misread():
    """OCR قرأ «khalid alqahtani» «knalid aigahtani» — والرقم سليم."""
    row = [_w("100/—", 871), _w("1600001893", 1025), _w("-", 1069),
           _w("knalid", 1092), _w("aigahtani", 1120)]
    boxes, counts, scrub = find_sensitive_boxes(row)
    assert counts == {"id_row": 1}
    box = boxes[0]
    centre = 1025 + 40
    assert box["left"] <= 871 and box["left"] + box["width"] >= 1120 + 72
    assert box["left"] <= centre - 250 and box["left"] + box["width"] >= centre + 250
    assert {"1600001893", "knalid", "aigahtani"} <= set(scrub)


def test_far_away_words_on_the_same_line_are_not_swept_into_the_row():
    line = [_w("Search", 40), _w("13111814360", 1016)]
    _boxes, _counts, scrub = find_sensitive_boxes(line)
    assert "Search" not in scrub


# ── الروابط والمسارات ────────────────────────────────────────────────
def test_url_keeps_the_domain_and_blurs_the_rest():
    url = "https://maarif.schoology.com/course/8510020295/assessments/8576286187"
    word = _w(url, 100, width=600)
    boxes, counts, scrub = find_sensitive_boxes([word])
    assert counts == {"url": 1}
    tail = boxes[0]
    assert 100 < tail["left"] < 100 + 600 * 0.6   # النطاق يبقى ظاهرًا
    assert tail["left"] + tail["width"] == pytest.approx(700)
    assert scrub_screen_text(f"Schoology\n{url}", scrub) == "Schoology\nmaarif.schoology.com"


def test_bare_domain_is_instructional_and_stays():
    boxes, counts, _ = find_sensitive_boxes([_w("ems.maarif.sa", 100)])
    assert boxes == [] and counts == {}


def test_oauth_query_is_blurred():
    word = _w("login.microsoftonline.com/common/oauth2/authorize?client_id=f97b3686", 100,
              width=700)
    _boxes, counts, _ = find_sensitive_boxes([word])
    assert counts == {"url": 1}


@pytest.mark.parametrize("path", [r"C:\Users\IBM\Desktop\Desktop", r"\\server\share",
                                  "/Users/mohammed/Documents"])
def test_file_paths_are_blurred(path):
    _boxes, counts, _ = find_sensitive_boxes([_w(path, 10)])
    assert counts == {"path": 1}


# ── الحجب قبل التخطيط ───────────────────────────────────────────────
def _keyframe(image_id, sensitive=()):
    from config.schemas import KeyframeMetadata
    return KeyframeMetadata(image_id=image_id, timestamp=60.0 * image_id + 9,
                            filename=f"{image_id}.jpg", scene_id=image_id,
                            change_score=0.5, width=10, height=10,
                            selection_reason="stable_frame",
                            sensitive=list(sensitive), privacy_scanned=True)


def _outputs(withhold=True):
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin

    config = AppConfig()
    config.document.withhold_sensitive_screens = withhold
    host = OutputsMixin.__new__(OutputsMixin)
    host.config = config
    host._plan_notes = []
    return host


def test_login_screens_are_withheld_and_the_user_is_told_when():
    host = _outputs()
    frames = [_keyframe(1, ["login"]), _keyframe(2), _keyframe(3, ["login"])]
    kept = host._withhold_sensitive(frames)
    assert [k.image_id for k in kept] == [2]
    note = host._plan_notes[0]
    assert note.startswith("privacy:") and "2 لقطة" in note
    assert "1:09" in note and "3:09" in note


def test_withholding_can_be_turned_off():
    host = _outputs(withhold=False)
    frames = [_keyframe(1, ["login"]), _keyframe(2)]
    assert host._withhold_sensitive(frames) == frames
    assert host._plan_notes == []


def test_old_keyframes_json_loads_without_the_new_fields():
    from config.schemas import KeyframeMetadata
    kf = KeyframeMetadata(image_id=1, timestamp=1.0, filename="a.jpg", scene_id=1,
                          change_score=0.0, width=1, height=1,
                          selection_reason="stable_frame", redacted=True)
    assert kf.sensitive == [] and kf.redactions == {}


# ── رسالة الموافقة ───────────────────────────────────────────────────
def _config(send_images=True, anonymize=True, withhold=True):
    return SimpleNamespace(
        rewrite=SimpleNamespace(send_images=send_images),
        document=SimpleNamespace(anonymize_names=anonymize,
                                 withhold_sensitive_screens=withhold))


def test_consent_no_longer_claims_images_are_never_sent():
    """كانت: «الفيديو والصوت والصور لا تُرسل إطلاقًا» — ثم أُرسلت 87 لقطة."""
    from utils.egress_notice import describe_egress

    text = describe_egress(_config(), "Claude API")
    assert "لقطات الشاشة" in text
    assert "الصور لا تُرسل" not in text
    assert "شاشات تسجيل الدخول لا تُرسل" in text
    assert "لا يُرسل الفيديو ولا الصوت" in text


def test_consent_for_text_only_does_not_mention_screenshots():
    from utils.egress_notice import describe_egress

    text = describe_egress(_config(send_images=False), "Claude API")
    assert "لقطات" not in text and "نصّ التفريغ" in text


def test_consent_warns_when_images_go_unredacted_and_names_the_study_provider():
    from utils.egress_notice import describe_egress

    text = describe_egress(_config(anonymize=False), "Claude API", "OpenAI")
    assert "دون طمس" in text
    assert "«OpenAI»" in text and "الحزمة التعليمية" in text


def test_url_read_without_the_colon_is_still_a_url():
    """OCR الفعلي: «https//maarif.schoology.com/course/8510020295/…»."""
    word = _w("https//maarif.schoology.com/course/8510020295/assessments/8576286", 113,
              width=310)
    _boxes, counts, scrub = find_sensitive_boxes([word])
    assert counts == {"url": 1}
    assert scrub_screen_text(word["text"], scrub) == "maarif.schoology.com"


def test_a_misread_id_in_the_id_column_still_anchors_its_row():
    """صفّ من ستة نجا لأن رقمه قُرئ «(1311182:»."""
    rows = [[_w("13111819712", 1008, line=(1, 1, i), top=380 + 64 * i),
             _w("سلطان", 1070, line=(1, 1, i), top=380 + 64 * i)] for i in range(2)]
    misread = [_w("(1311182:", 1014, line=(1, 1, 9), top=508),
               _w("دارس", 1081, line=(1, 1, 9), top=508)]
    stray = [_w("4,656", 400, line=(1, 1, 10), top=600)]   # رقم خارج العمود
    _boxes, counts, scrub = find_sensitive_boxes(
        [w for row in rows for w in row] + misread + stray)
    assert counts["id_row"] >= 3                     # + صفّا الطرفين المستنتجان
    assert "دارس" in scrub and "4,656" not in scrub


def test_unread_rows_of_a_regular_student_table_are_inferred():
    """صفٌّ لم يُقرأ رقمه في المنتصف، وصفٌّ أعلى الجدول تحت قائمة منسدلة."""
    tops = [380, 444, 508, 572, 700]                 # 636 لم يُقرأ
    words = [_w("13111819712", 1008, line=(1, 1, i), top=t) for i, t in enumerate(tops)]
    boxes, counts, _ = find_sensitive_boxes(words)
    assert counts["id_row"] == 5 + 3                 # 636 + 316 + 764
    inferred = sorted(round(b["top"]) for b in boxes[5:])
    assert inferred == [316, 636, 764]


def test_two_ids_are_not_enough_to_infer_a_table():
    words = [_w("13111819712", 1008, line=(1, 1, i), top=t)
             for i, t in enumerate([380, 444])]
    _boxes, counts, _ = find_sensitive_boxes(words)
    assert counts == {"id_row": 2}


def test_a_url_with_home_in_its_path_is_a_url_not_a_file_path():
    _boxes, counts, _ = find_sensitive_boxes(
        [_w("maarif.schoology.com/home/recent-activity", 100, width=300)])
    assert counts == {"url": 1}


def test_network_path_with_a_staff_username_is_blurred():
    """اللقطة 44: مستكشف الملفات يعرض مسار شبكة فيه اسم حساب موظف."""
    _boxes, counts, scrub = find_sensitive_boxes(
        [_w(r"(\\172.20.54.173\Users\A.Metwoly\Desktop", 300)])
    assert counts == {"path": 1}


def test_unscanned_frames_are_reported_not_assumed_safe():
    """1.19.1: بلا Tesseract لا تُفترض اللقطة آمنة لأن ``sensitive`` فارغ."""
    from config.schemas import KeyframeMetadata
    host = _outputs()
    frames = [KeyframeMetadata(image_id=1, timestamp=9.0, filename="1.jpg", scene_id=1,
                               change_score=0.5, width=10, height=10,
                               selection_reason="stable_frame")]
    assert host._withhold_sensitive(frames) == frames
    assert "لم تُفحص 1 من 1 لقطة" in host._plan_notes[0]
