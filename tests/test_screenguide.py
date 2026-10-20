"""«سجّلني — دليل خطوة بخطوة»: كل نقرة في أي برنامج خطوةٌ مرقّمة بصورتها.

منقولة من اختبارات WebDoc (``test_screen_recording.py``) بمصدرٍ يعيد تشغيل ما
فعله شخص — نقرة في Excel، كتابة في حقل، حوار يُفتح، اختصار — ثم يُفحص الدليل
كما يفحصه قارئ: الخطوات خطواته بترتيبه، مسمّاة بأسماء البرنامج نفسه، لكلٍّ
صورتها ورقمها، ولا شيء مما كتبه فيه.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import List, Optional

import pytest
from PIL import Image

from screenguide import prose
from screenguide.guide import Guide, build_plan, list_guides, poster_for, write_guide
from screenguide.model import (
    CHECK,
    SUBMIT,
    TYPE,
    UNCHECK,
    RecordedStep,
    Recording,
    ScreenEvent,
    ScreenEventSource,
    ScreenTarget,
    ScreenWindow,
)
from screenguide.recorder import ScreenGuideRecorder

EXCEL = ScreenWindow(app="Microsoft Excel", title="Budget.xlsx - Excel", process="excel")
SAVE_AS = ScreenWindow(app="Microsoft Excel", title="Save As", process="excel", dialog=True)
ADMIN = ScreenWindow(app="Registry Editor", title="Registry Editor", process="regedit",
                     protected=True)


def frame(colour: str = "white") -> Image.Image:
    return Image.new("RGB", (800, 600), colour)


def click(name: str, role: str, box, *, at: int, window: ScreenWindow = EXCEL,
          button: str = "left", checked=None, point=None) -> ScreenEvent:
    x, y = point or (box[0] + box[2] / 2, box[1] + box[3] / 2)
    return ScreenEvent(
        kind="click", at_ms=at, button=button, point=(x, y), screen_point=(x, y),
        frame=frame(), window=window,
        target=ScreenTarget(name=name, role=role, box=box, checked=checked,
                            editable=role == "textbox"))


class Playback(ScreenEventSource):
    """جلسة شخص تُعاد حدثًا حدثًا."""

    def __init__(self, events: List[ScreenEvent], *, final: Optional[ScreenWindow] = EXCEL):
        self._events = list(events)
        self._final = final
        self.shown: List[int] = []
        self.closed = False

    def next_event(self, timeout=None):
        if self._events:
            return self._events.pop(0)
        if self._final is None:
            return ScreenEvent(kind="finish")
        return ScreenEvent(kind="finish", frame=frame("lightgrey"), window=self._final)

    def show_steps(self, steps: int) -> None:
        self.shown.append(steps)

    def request_finish(self) -> None:
        self._events.clear()

    def close(self) -> None:
        self.closed = True


def record(tmp_path: Path, events, *, language="ar", final=EXCEL, title="حفظ الملف باسم جديد"):
    source = Playback(events, final=final)
    folder = tmp_path / "journey"
    recording = ScreenGuideRecorder().record(source, title=title, folder=folder,
                                             language=language)
    return recording, source, folder


JOURNEY = [
    click("File", "menuitem", (10, 10, 40, 20), at=1000),
    click("Save As", "menuitem", (10, 120, 120, 24), at=3000),
    click("File name", "textbox", (200, 400, 300, 24), at=5000, window=SAVE_AS),
    ScreenEvent(kind="type", at_ms=5600, frame=frame(), window=SAVE_AS,
                target=ScreenTarget(name="File name", role="textbox")),
    click("Save", "button", (600, 540, 80, 28), at=8000, window=SAVE_AS),
]


# ------------------------------------------------------------------ التسجيل
def test_every_click_in_any_program_is_a_numbered_step(tmp_path):
    recording, source, folder = record(tmp_path, JOURNEY)
    steps = recording.steps
    assert [s.index for s in steps] == [1, 2, 3, 4]
    assert [s.label for s in steps] == ["File", "Save As", "File name", "Save"]
    # انقر الحقل ثم اكتب: خطوة واحدة «أدخل البيانات في حقل File name».
    assert steps[2].action == TYPE
    assert all(s.shot and (folder / s.shot).is_file() for s in steps)
    assert all(s.box for s in steps)
    assert all(s.app == "Microsoft Excel" for s in steps)
    assert source.shown[-1] == 4
    # «Save As» فتح حوارًا: إليه قادت الخطوة 2.
    assert steps[1].moved and steps[1].arrived_at == "Save As"
    assert steps[1].arrived_kind == "dialog"
    # و«Save» أغلقه عائدًا إلى Excel — باسم البرنامج لا اسم الملف.
    assert steps[3].arrived_at == "Microsoft Excel"
    assert recording.final_shot and (folder / recording.final_shot).is_file()
    # الرحلة محفوظة وتُقرأ كما كُتبت.
    again = Recording.load(folder)
    assert [s.to_dict() for s in again.steps] == [s.to_dict() for s in steps]


def test_the_guide_reads_like_the_journey(tmp_path):
    recording, _, _ = record(tmp_path, [JOURNEY[0], JOURNEY[1], JOURNEY[4]])
    texts = prose.instructions(recording)
    assert texts[0] == "اختر «File» من القائمة في «Microsoft Excel»."
    # البرنامج يُذكر مرةً حيث تدخله الرحلة، لا في كل سطر.
    assert texts[1] == "اختر «Save As» من القائمة."
    assert texts[2] == "اضغط على «Save»."
    assert prose.outcome(recording.steps[1], "ar") == "تظهر نافذة «Save As»."
    assert "على الجهاز" in prose.description(recording)
    assert prose.start_line(recording) == "ابدأ من نافذة «Microsoft Excel»."


def test_double_click_right_click_and_drag_are_one_step_each(tmp_path):
    icon = (100, 100, 64, 64)
    events = [
        click("Report.docx", "option", icon, at=1000),
        click("Report.docx", "option", icon, at=1200),       # الضغطة الثانية: مزدوجة
        click("Report.docx", "option", icon, at=4000, button="right"),
        click("Sheet1", "tab", (20, 560, 60, 20), at=7000),
        ScreenEvent(kind="up", at_ms=7400, button="left", screen_point=(300.0, 570.0)),
    ]
    recording, _, _ = record(tmp_path, events, language="en")
    steps = recording.steps
    assert [s.gesture for s in steps] == ["double", "right", "drag"]
    assert prose.instruction(steps[0], "en") == "Double-click “Report.docx”."
    assert prose.instruction(steps[1], "ar") == "انقر بزر الفأرة الأيمن على «Report.docx»."
    assert prose.instruction(steps[2], "en") == "Drag “Sheet1” to its new place."


def test_two_slow_clicks_are_two_steps(tmp_path):
    button = (10, 10, 80, 24)
    events = [click("Bold", "button", button, at=1000), click("Bold", "button", button, at=2500)]
    recording, _, _ = record(tmp_path, events)
    assert len(recording.steps) == 2
    assert all(s.gesture is None for s in recording.steps)


def test_a_password_is_never_shown_and_never_written(tmp_path):
    field = (200, 200, 240, 24)
    events = [
        ScreenEvent(kind="click", at_ms=1000, point=(250, 210), screen_point=(250, 210),
                    frame=frame(), window=EXCEL,
                    target=ScreenTarget(name="Password", role="textbox", box=field,
                                        secret=True, editable=True)),
        ScreenEvent(kind="type", at_ms=1500, frame=frame(), window=EXCEL,
                    target=ScreenTarget(name="Password", role="textbox", secret=True)),
    ]
    recording, _, folder = record(tmp_path, events)
    step = recording.steps[0]
    assert step.action == TYPE and step.secret
    assert prose.instruction(step, "ar") == "أدخل كلمة المرور في حقل «Password»."
    assert prose.instruction(step, "ar", app="Microsoft Excel") == \
        "أدخل كلمة المرور في حقل «Password» في «Microsoft Excel»."
    image = Image.open(folder / step.shot)
    assert image.getpixel((210, 210)) == (32, 32, 32)      # مطليّ في الصورة المحفوظة
    assert image.getpixel((100, 100)) == (255, 255, 255)
    # ولا قيمة مكتوبة في أي مكان من الرحلة المحفوظة.
    assert "value" not in (folder / "recording.json").read_text(encoding="utf-8")


def test_shortcuts_are_steps_by_name(tmp_path):
    events = [
        ScreenEvent(kind="shortcut", keys="Ctrl+S", at_ms=1000, frame=frame(), window=EXCEL),
        ScreenEvent(kind="enter", at_ms=2000, frame=frame(), window=EXCEL,
                    target=ScreenTarget(name="Name Box", role="combobox",
                                        box=(5, 30, 80, 20))),
    ]
    recording, _, _ = record(tmp_path, events)
    first, second = recording.steps
    assert first.role == "shortcut" and first.label == "Ctrl+S"
    assert prose.instruction(first, "ar", app="Microsoft Excel") == \
        "اضغط مفاتيح الاختصار «Ctrl+S» في «Microsoft Excel»."
    assert second.action == SUBMIT
    assert prose.instruction(second, "en") == "Press Enter in “Name Box”."


def test_a_program_running_as_administrator_is_named_by_its_window(tmp_path):
    events = [ScreenEvent(kind="click", at_ms=1000, point=(400, 300),
                          screen_point=(400, 300), frame=frame(), window=ADMIN)]
    recording, _, _ = record(tmp_path, events)
    step = recording.steps[0]
    assert step.label == "Registry Editor" and step.role == "pane"
    assert step.box == (382.0, 282.0, 36.0, 36.0)
    assert prose.instruction(step, "ar") == \
        "اضغط على الموضع المحدد في الصورة داخل «Registry Editor»."


def test_a_huge_control_marks_the_spot_not_the_whole_grid(tmp_path):
    events = [click("Grid", "cell", (0, 100, 800, 500), at=1000, point=(420, 330))]
    recording, _, _ = record(tmp_path, events)
    assert recording.steps[0].box == (402.0, 312.0, 36.0, 36.0)


def test_checkbox_state_decides_check_or_uncheck(tmp_path):
    events = [
        click("Show gridlines", "checkbox", (40, 40, 16, 16), at=1000, checked=True),
        click("Wrap text", "checkbox", (40, 80, 16, 16), at=3000, checked=False),
    ]
    recording, _, _ = record(tmp_path, events)
    assert [s.action for s in recording.steps] == [UNCHECK, CHECK]


def test_undo_drops_the_last_step_and_its_picture(tmp_path):
    events = [
        click("File", "menuitem", (10, 10, 40, 20), at=1000),
        click("Close", "menuitem", (10, 200, 80, 24), at=3000),
        ScreenEvent(kind="undo"),
        click("Save", "menuitem", (10, 100, 80, 24), at=6000),
    ]
    recording, source, folder = record(tmp_path, events)
    assert [s.label for s in recording.steps] == ["File", "Save"]
    assert [s.index for s in recording.steps] == [1, 2]
    assert source.shown == [1, 2, 1, 2]
    assert len(list((folder / "shots").glob("0*.png"))) == 2


def test_cancel_raises_and_writes_no_journey(tmp_path):
    from screenguide.recorder import RecordingCancelled

    with pytest.raises(RecordingCancelled):
        ScreenGuideRecorder().record(Playback(JOURNEY), title="x", folder=tmp_path / "j",
                                     should_stop=lambda: True)
    assert not (tmp_path / "j" / "recording.json").exists()


# --------------------------------------------------------------------- الدليل
def _guide_with(tmp_path: Path, title: str, events, guide_title="دليل Excel") -> Guide:
    guide = Guide.open(tmp_path, guide_title, "ar")
    recording, _, folder = record(tmp_path / "raw" / title, events, title=title)
    target = guide.new_journey_folder(recording.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(target)
    guide.add(recording)
    return guide


def test_a_second_journey_adds_a_procedure_and_the_same_name_replaces(tmp_path):
    guide = _guide_with(tmp_path, "حفظ الملف", JOURNEY)
    guide = _guide_with(tmp_path, "طباعة الملف", JOURNEY[:2])
    assert [j.recording.title for j in guide.journeys()] == ["حفظ الملف", "طباعة الملف"]
    old_id = guide.journeys()[0].id
    guide = _guide_with(tmp_path, "حفظ  الملف", JOURNEY[:1])
    titles = [j.recording.title for j in guide.journeys()]
    assert titles == ["طباعة الملف", "حفظ  الملف"]
    assert not guide.new_journey_folder(old_id).exists()
    assert list_guides(tmp_path) == ["دليل Excel"]


def test_edits_survive_and_apply_in_every_format(tmp_path):
    guide = _guide_with(tmp_path, "حفظ الملف", JOURNEY)
    jid = guide.journeys()[0].id
    guide.edit(jid, 1).text = "افتح قائمة «ملف»."
    guide.edit(jid, 2).no_picture = True
    guide.edit(jid, 3).deleted = True
    guide.systems["Microsoft Excel"] = "إكسل"
    guide.save()

    guide = Guide.open(tmp_path, "دليل Excel")
    steps = guide.steps(guide.journeys()[0])
    assert [s.number for s in steps] == [1, 2, 3]          # أُعيد الترقيم بعد الحذف
    assert steps[0].text == "افتح قائمة «ملف»." and steps[0].edited
    assert steps[1].picture is None
    assert steps[2].text == "اضغط على «Save»."
    assert steps[0].group == "إكسل"

    plan, total = build_plan(guide, guide.folder / "keyframes")
    assert total == 3
    texts = [b.text for b in plan.sections[0].blocks if b.kind == "step"]
    assert texts[0] == "افتح قائمة «ملف»."
    assert plan.sections[0].blocks[0].text == "ابدأ من نافذة «إكسل»."
    figures = [b for b in plan.sections[0].blocks if b.kind == "figure"]
    # خطوتان بصورة (الثانية بلا صورة) + صورة النتيجة.
    assert len(figures) == 3 and figures[-1].caption.startswith("النتيجة")
    assert plan.total_figures_in == plan.total_figures_out == 3


def test_the_numbered_picture_marks_the_control(tmp_path):
    from screenguide.pictures import BADGE, annotate

    shot = tmp_path / "s.png"
    Image.new("RGB", (1600, 900), "white").save(shot)
    out = annotate(shot, (700, 400, 120, 40), 3, tmp_path / "o.png")
    image = Image.open(out).convert("RGB")
    colours = set(image.getdata())
    assert BADGE in colours                       # رقم الخطوة مرسوم
    assert any(c[1] > c[0] + 40 and c[2] > c[0] + 40 for c in colours)  # إطار فيروزي


def test_write_guide_makes_word_html_and_an_infographic(tmp_path):
    from config.settings import AppConfig

    guide = _guide_with(tmp_path, "حفظ الملف", JOURNEY)
    config = AppConfig()
    outputs = write_guide(guide, config, formats=["html"], infographics=True)
    assert outputs.docx is not None and outputs.docx.is_file()
    assert outputs.procedures == 1 and outputs.steps == 4
    with zipfile.ZipFile(outputs.docx) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
        body = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
        media = [n for n in archive.namelist() if n.startswith("word/media/")]
    assert "حفظ الملف" in body and "«Save As»" in body
    assert "دليل إجرائي مصوّر" in body
    assert len(media) >= 5                       # 4 خطوات + النتيجة (+ الشعار)
    html_page = next(p for p in outputs.others if p.suffix == ".html")
    text = html_page.read_text(encoding="utf-8")
    assert "الخطوات: 4" in text and 'id="vid"' not in text
    posters = [p for p in outputs.infographics if p.suffix == ".html"]
    assert len(posters) == 1 and "«Save&nbsp;As»" in posters[0].read_text(encoding="utf-8")
    assert (guide.folder / "plan.json").is_file()


def test_one_journey_one_poster_grouped_by_system(tmp_path):
    from screenguide.infographic import groups_of, poster_html

    noor = ScreenWindow(app="Google Chrome", title="نور", process="chrome")
    events = [click("الطلاب", "link", (10, 10, 80, 20), at=1000, window=noor),
              *JOURNEY[:2]]
    guide = _guide_with(tmp_path, "تصدير الدرجات", events)
    guide.systems["Google Chrome"] = "نظام نور"
    guide.poster_titles[guide.journeys()[0].id] = "تصدير درجات الطلاب"
    poster = poster_for(guide, guide.journeys()[0])
    assert poster.title == "تصدير درجات الطلاب"
    assert [g for g, _ in groups_of(poster.steps)] == ["نظام نور", "Microsoft Excel"]
    page = poster_html(poster)
    assert "أولاً: في «نظام نور»" in page and "ثانياً: في «Microsoft Excel»" in page
    assert all(s.picture for s in poster.steps)


def test_poster_png_is_drawn_with_qt(tmp_path):
    # ‏QtGui يحتاج مكتبات رسومية (libEGL) قد تغيب عن خادم اختبار بلا شاشة.
    pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from screenguide.infographic import render_png

    _app = QApplication.instance() or QApplication([])
    guide = _guide_with(tmp_path, "حفظ الملف", JOURNEY)
    out = render_png(poster_for(guide, guide.journeys()[0]), tmp_path / "p.png")
    image = Image.open(out)
    assert image.width == 3200 and image.height > 1500


# ---------------------------------------------------------------- السياسة
def test_screen_guide_is_refused_where_it_cannot_work(monkeypatch):
    import screenguide

    monkeypatch.setattr(screenguide.sys, "platform", "linux")
    assert screenguide.screen_guide_supported() is False
    with pytest.raises(RuntimeError):
        screenguide.open_screen_recorder()


def test_an_administrator_can_switch_it_off(monkeypatch, tmp_path):
    import screenguide
    from config import policy as policy_module
    from config.settings import AppConfig

    monkeypatch.setattr(screenguide.sys, "platform", "win32")
    monkeypatch.setenv("MOEEN_SCREEN_GUIDE", "0")
    assert screenguide.screen_guide_supported() is False

    path = tmp_path / "policy.yaml"
    path.write_text("allow_screen_guide: false\n", encoding="utf-8")
    config = AppConfig()
    policy = policy_module.apply(config, policy_module.load(path))
    assert config.screen_guide.enabled is False
    assert "allow_screen_guide" in policy.locked


def test_recording_round_trips_through_json():
    step = RecordedStep(index=1, action=TYPE, label="Name", role="textbox", secret=True,
                        shot="shots/0001.png", box=(1.0, 2.0, 3.0, 4.0),
                        screen_before="excel/microsoft-excel", screen_after="excel/save-as",
                        arrived_at="Save As", arrived_kind="dialog", gesture=None,
                        app="Microsoft Excel", window="Microsoft Excel", at_seconds=1.5)
    raw = json.loads(json.dumps(Recording(title="t", steps=[step]).to_dict()))
    again = Recording.from_dict(raw)
    assert again.steps[0] == step


@pytest.mark.parametrize(("vk", "mods", "expected"), [
    (0x53, {"ctrl": True}, "Ctrl+S"),
    (0x74, {}, "F5"),
    (0x41, {"ctrl": True, "alt": True}, None),     # AltGr: كتابة لا اختصار
    (0x09, {"alt": True}, None),                   # Alt+Tab: تنقّل بين البرامج
    (0x41, {"shift": True}, None),                 # حرف كبير
    (0x2E, {"ctrl": True, "shift": True}, "Ctrl+Shift+Delete"),
])
def test_shortcuts_are_named_as_a_reader_presses_them(vk, mods, expected):
    import sys

    if sys.platform != "win32":
        pytest.skip("windows_source يحمّل مكتبات ويندوز")
    from screenguide.windows_source import _shortcut

    flags = {"ctrl": False, "alt": False, "shift": False, "win": False, **mods}
    assert _shortcut(vk, **flags) == expected


# ------------------------------------------------------------ المهام السابقة
def test_a_guide_is_listed_with_the_previous_jobs(tmp_path):
    from config.settings import AppConfig
    from core.job_registry import list_jobs

    guide = _guide_with(tmp_path, "حفظ الملف", JOURNEY)
    write_guide(guide, AppConfig(), formats=[], infographics=False)
    jobs = list_jobs(tmp_path, include_guides=True)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.is_guide and job.guide_title == "دليل Excel"
    assert job.name == "دليل: دليل Excel" and job.is_complete
    assert job.document is not None and job.document.suffix == ".docx"
    assert job.can_rebuild and not job.can_resume
    # الدمج وفهرس المقرّر والإحصاءات لا يرونه.
    assert list_jobs(tmp_path) == []
