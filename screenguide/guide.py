"""الدليل: كل رحلةٍ مسجَّلة إجراءٌ فيه، ومنه Word وPDF وصفحة ويب وإنفوجرافيك.

كما في WebDoc:

* **تسجيل رحلة أخرى يضيف إجراءً إلى الدليل نفسه**، وتسجيلها ثانيةً بالاسم
  نفسه يستبدل التسجيل الأقدم. الدليل هو كل رحلةٍ حالية، إجراءٌ لكلٍّ منها.
* **تعديل نصّ خطوة** يبقى فوق ما كُتب آليًّا ويعبر إعادة الإخراج؛ ومسح
  التعديل يعيد النصّ المولَّد. و**حذف الصورة من خطوة** حين لا تضيف شيئًا.
* **الإنفوجرافيك صورةٌ واحدة لكل رحلة** — لا رحلتان في صورة — بعنوانٍ وأسماء
  أنظمةٍ يغيّرها الشخص («نظام نور» بدل اسم البرنامج) وتُحفظ للمرة القادمة.

البنية على القرص (كلّها داخل مجلد الدليل)::

    <الدليل>/
      guide.json                 العنوان واللغة وترتيب الرحلات والتعديلات
      journeys/<id>/recording.json + shots/*.png   الرحلة الخام
      keyframes/*.png            صور الخطوات المرقّمة (للمستند والويب)
      <الدليل>.docx / .html / .pdf
      infographics/<الإجراء>.png و.html
      plan.json                  خطة المستند (للتدقيق وإعادة البناء)

لا نموذج لغوي ولا اتصال: الجملة محدَّدة تمامًا بما رُصد.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

from screenguide import prose
from screenguide.model import Recording

GUIDES_FOLDER = "أدلة خطوة بخطوة"
GUIDE_SCHEMA = "1.0"
#: الصيغ التي يكتبها الدليل بجانب Word. ‏pdf يحتاج Word أو LibreOffice، وغيابهما
#: سطرٌ في السجلّ لا فشل.
GUIDE_FORMATS = ("html", "pdf")

Log = Callable[[str], None]


def guides_root(output_dir: Path) -> Path:
    return Path(output_dir) / GUIDES_FOLDER


def safe_name(text: str, fallback: str = "دليل") -> str:
    """اسمٌ صالح لمجلد أو ملف على ويندوز، بالعربية كما هو."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:80].strip()
    return name or fallback


def list_guides(output_dir: Path) -> List[str]:
    root = guides_root(output_dir)
    if not root.is_dir():
        return []
    names = []
    for child in sorted(root.iterdir()):
        if (child / "guide.json").is_file():
            try:
                names.append(json.loads((child / "guide.json").read_text(
                    encoding="utf-8")).get("title") or child.name)
            except Exception:
                names.append(child.name)
    return names


@dataclass
class StepEdit:
    #: نصٌّ يحلّ محلّ المولَّد في كل الصيغ. فارغ = النصّ المولَّد.
    text: str = ""
    no_picture: bool = False
    deleted: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in (("text", self.text), ("no_picture", self.no_picture),
                                  ("deleted", self.deleted)) if v}

    @property
    def empty(self) -> bool:
        return not (self.text.strip() or self.no_picture or self.deleted)


@dataclass
class GuideStep:
    """خطوةٌ كما تظهر في الدليل، بعد التعديلات وإعادة الترقيم."""
    number: int
    original_index: int
    text: str
    generated: str
    result: Optional[str]
    picture: Optional[Path]
    box: Optional[tuple]
    group: Optional[str]
    edited: bool = False


@dataclass
class Journey:
    recording: Recording
    folder: Path

    @property
    def id(self) -> str:
        return self.recording.id


@dataclass
class Guide:
    folder: Path
    title: str
    language: str = "ar"
    order: List[str] = field(default_factory=list)
    #: ‏``journey_id -> {step_index(str): StepEdit}``
    edits: Dict[str, Dict[str, StepEdit]] = field(default_factory=dict)
    #: ‏``journey_id -> عنوان الإنفوجرافيك``
    poster_titles: Dict[str, str] = field(default_factory=dict)
    #: ‏``اسم البرنامج كما رُصد -> الاسم الذي يُطبع`` («chrome» ← «نظام نور»).
    systems: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------ تحميل/حفظ
    @classmethod
    def open(cls, output_dir: Path, title: str, language: str = "ar") -> "Guide":
        folder = guides_root(output_dir) / safe_name(title)
        guide = cls(folder=folder, title=title.strip() or folder.name, language=language)
        path = folder / "guide.json"
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            guide.title = raw.get("title") or guide.title
            guide.language = raw.get("language") or language
            guide.order = list(raw.get("journeys") or [])
            guide.edits = {
                jid: {k: StepEdit(**v) for k, v in (steps or {}).items()}
                for jid, steps in (raw.get("edits") or {}).items()}
            guide.poster_titles = dict(raw.get("poster_titles") or {})
            guide.systems = dict(raw.get("systems") or {})
        return guide

    def save(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": GUIDE_SCHEMA,
            "title": self.title,
            "language": self.language,
            "journeys": self.order,
            "edits": {jid: {k: e.to_dict() for k, e in steps.items() if not e.empty}
                      for jid, steps in self.edits.items()},
            "poster_titles": {k: v for k, v in self.poster_titles.items() if v.strip()},
            "systems": {k: v for k, v in self.systems.items() if v.strip()},
        }
        data["edits"] = {k: v for k, v in data["edits"].items() if v}
        path = self.folder / "guide.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    # ------------------------------------------------------------- الرحلات
    def new_journey_folder(self, recording_id: str) -> Path:
        return self.folder / "journeys" / recording_id

    def journeys(self) -> List[Journey]:
        out: List[Journey] = []
        for jid in self.order:
            folder = self.new_journey_folder(jid)
            try:
                out.append(Journey(Recording.load(folder), folder))
            except Exception:
                continue
        return out

    def add(self, recording: Recording) -> Optional[str]:
        """يضيف رحلةً مسجَّلة؛ ويعيد عنوان رحلةٍ استُبدلت إن كان لها الاسم نفسه."""
        replaced = None
        key = _key(recording.title)
        for journey in self.journeys():
            if journey.id != recording.id and _key(journey.recording.title) == key:
                replaced = journey.recording.title
                self.remove(journey.id)
        if recording.id not in self.order:
            self.order.append(recording.id)
        self.save()
        return replaced

    def remove(self, journey_id: str) -> None:
        if journey_id in self.order:
            self.order.remove(journey_id)
        self.edits.pop(journey_id, None)
        self.poster_titles.pop(journey_id, None)
        shutil.rmtree(self.new_journey_folder(journey_id), ignore_errors=True)
        self.save()

    def edit(self, journey_id: str, index: int) -> StepEdit:
        return self.edits.setdefault(journey_id, {}).setdefault(str(index), StepEdit())

    # ------------------------------------------------------------- الخطوات
    def system_name(self, app: Optional[str]) -> Optional[str]:
        if not app:
            return None
        return (self.systems.get(app) or "").strip() or app

    def steps(self, journey: Journey) -> List[GuideStep]:
        """خطوات الرحلة كما تُطبع: التعديلات مطبَّقة والمحذوف خارجها، مُعاد ترقيمها."""
        recording = journey.recording
        generated = prose.instructions(recording)
        edits = self.edits.get(journey.id, {})
        out: List[GuideStep] = []
        for step, text in zip(recording.steps, generated):
            edit = edits.get(str(step.index)) or StepEdit()
            if edit.deleted:
                continue
            # الاسم المطبوع للبرنامج يحلّ محلّ المرصود في الجملة المولَّدة.
            if step.app and self.system_name(step.app) != step.app:
                text = text.replace(prose.q(step.app, recording.language),
                                    prose.q(self.system_name(step.app), recording.language))
            picture = None
            if step.shot and not edit.no_picture:
                candidate = journey.folder / step.shot
                picture = candidate if candidate.is_file() else None
            out.append(GuideStep(
                number=len(out) + 1, original_index=step.index,
                text=edit.text.strip() or text, generated=text,
                result=prose.outcome(step, recording.language), picture=picture,
                box=step.box, group=self.system_name(step.app),
                edited=bool(edit.text.strip())))
        return out


def _key(title: str) -> str:
    return " ".join((title or "").split()).casefold()


# ===================================================================== الإخراج
@dataclass
class GuideOutputs:
    docx: Optional[Path] = None
    others: List[Path] = field(default_factory=list)
    infographics: List[Path] = field(default_factory=list)
    procedures: int = 0
    steps: int = 0

    @property
    def all(self) -> List[Path]:
        return [p for p in [self.docx, *self.others, *self.infographics] if p]


def build_plan(guide: Guide, images_dir: Path):
    """الدليل ← ``DocumentPlan``: إجراءٌ لكل رحلة، وخطواتٌ مرقّمة بصورها.

    تعيد ``(plan, عدد الخطوات)``. تكتب صور الخطوات المعلَّمة في ``images_dir``.
    """
    from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
    from screenguide.pictures import annotate

    ar = guide.language == "ar"
    images_dir.mkdir(parents=True, exist_ok=True)
    for old in images_dir.glob("*.png"):
        old.unlink(missing_ok=True)

    sections: List[DocumentSection] = []
    image_id = 0
    total_steps = 0
    for number, journey in enumerate(guide.journeys(), start=1):
        steps = guide.steps(journey)
        if not steps:
            continue
        recording = journey.recording
        blocks: List[DocumentBlock] = []
        start = prose.start_line(recording)
        if start:
            if recording.steps and recording.steps[0].app:
                app = recording.steps[0].app
                start = start.replace(prose.q(app, guide.language),
                                      prose.q(guide.system_name(app), guide.language))
            blocks.append(DocumentBlock(kind="paragraph", text=start))
        for step in steps:
            text = step.text if not step.result or step.edited else f"{step.text} {step.result}"
            blocks.append(DocumentBlock(kind="step", text=text))
            if step.picture is None:
                continue
            image_id += 1
            name = f"j{number:02d}-s{step.number:03d}.png"
            annotate(step.picture, step.box, step.number, images_dir / name)
            # التعليق رقم الخطوة وحده: نصّها فوق الصورة مباشرةً، وتكراره تحتها
            # ضجيج. ويبقى فهرس الأشكال والنصّ البديل مفهومين.
            caption = f"الخطوة {step.number}" if ar else f"Step {step.number}"
            blocks.append(DocumentBlock(kind="figure", image_id=image_id,
                                        image_filename=name, caption=caption))
        total_steps += len(steps)
        results = prose.result_lines(recording)
        final = journey.folder / recording.final_shot if recording.final_shot else None
        if final is not None and final.is_file():
            image_id += 1
            name = f"j{number:02d}-result.png"
            annotate(final, None, None, images_dir / name)
            caption = (results[0] if results else
                       ("الشاشة عند انتهاء الإجراء." if ar else "The screen at the end."))
            blocks.append(DocumentBlock(
                kind="figure", image_id=image_id, image_filename=name,
                caption=(f"النتيجة: {caption}" if ar else f"Result: {caption}")))
        elif results:
            blocks.append(DocumentBlock(
                kind="note", text=(f"النتيجة: {results[0]}" if ar else f"Result: {results[0]}")))
        sections.append(DocumentSection(
            title=recording.title, level=1,
            summary=prose.description(recording, len(steps)), blocks=blocks))

    plan = DocumentPlan(
        title=guide.title,
        subtitle=("دليل إجرائي مصوّر — مسجَّل من تنفيذ فعلي" if ar
                  else "Illustrated procedure guide — recorded from a real run"),
        sections=sections,
        generated_by="recording",
        total_figures_in=image_id,
        total_figures_out=image_id,
    )
    return plan, total_steps


def write_guide(guide: Guide, config, *, formats: Optional[List[str]] = None,
                infographics: bool = True, log: Optional[Log] = None) -> GuideOutputs:
    """يكتب الدليل كاملًا: Word أولًا، ثم الصيغ الأخرى والإنفوجرافيك."""
    from config.schemas import VideoMetadata
    from document.exporters import ExportContext, write_exports
    from document.word_generator import DocumentGenerator

    say = log or (lambda _msg: None)
    guide.folder.mkdir(parents=True, exist_ok=True)
    images_dir = guide.folder / "keyframes"
    plan, steps = build_plan(guide, images_dir)
    outputs = GuideOutputs(procedures=len(plan.sections), steps=steps)
    if not plan.sections:
        say("لا خطوات في الدليل بعد — لم يُكتب شيء.")
        return outputs

    (guide.folder / "plan.json").write_text(plan.model_dump_json(indent=2),
                                            encoding="utf-8")
    base = guide.folder / f"{safe_name(guide.title)}.docx"
    duration = sum(j.recording.duration_seconds for j in guide.journeys()) or 1.0
    metadata = VideoMetadata(
        filename=("تسجيل خطوات على الجهاز" if guide.language == "ar"
                  else "Recorded on the computer"),
        path=guide.folder, duration_seconds=max(1.0, duration),
        width=0, height=0, fps=0.0, codec="screen", has_audio=False, has_video=False)
    document_config = config.document
    outputs.docx = DocumentGenerator(document_config).generate(
        plan, metadata, images_dir, base)
    say(f"كُتب الدليل: {base.name}")

    wanted = [f for f in (formats if formats is not None else GUIDE_FORMATS)
              if f in GUIDE_FORMATS]
    if wanted:
        ctx = ExportContext(plan=plan, metadata=metadata, images_dir=images_dir,
                            base_path=base, docx_path=outputs.docx,
                            document_config=document_config,
                            options={"procedures": len(plan.sections), "steps": steps})
        outputs.others = write_exports(ctx, wanted)
    if infographics:
        from screenguide.infographic import write_infographics

        outputs.infographics = write_infographics(guide, log=say)
    return outputs


def poster_for(guide: Guide, journey: Journey):
    """رحلةٌ واحدة ← ``Poster`` (صورة واحدة، لا رحلتان فيها)."""
    from screenguide.infographic import Poster, PosterStep
    from screenguide.pictures import step_picture

    recording = journey.recording
    steps = guide.steps(journey)
    poster_steps = []
    for step in steps:
        picture = step_picture(step.picture, step.box, step.number) if step.picture else None
        poster_steps.append(PosterStep(number=step.number, instruction=step.text,
                                       result=None if step.edited else step.result,
                                       picture=picture, group=step.group))
    groups = {s.group for s in poster_steps if s.group}
    start = prose.start_line(recording)
    if start and recording.steps and recording.steps[0].app:
        app = recording.steps[0].app
        start = start.replace(prose.q(app, guide.language),
                              prose.q(guide.system_name(app), guide.language))
    return Poster(
        title=(guide.poster_titles.get(journey.id) or "").strip() or recording.title,
        steps=poster_steps,
        language=guide.language,
        source=guide.title if _key(guide.title) != _key(recording.title) else "",
        system=next(iter(groups)) if len(groups) == 1 else "",
        start=[start] if start else [],
        results=prose.result_lines(recording),
        made_on=date.today(),
    )
