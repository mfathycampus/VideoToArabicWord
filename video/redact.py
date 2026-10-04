"""إخفاء أسماء الأشخاص من لقطات الشاشة ونصّها.

سبب وجوده: تسجيل «متابعة تحضير المعلمين» يعرض قائمة معلّمات بأسمائهن
الكاملة وحالة تحضير كلٍّ منهن («Not Met»، «0 / 5 Lessons»). المستند
يُوزَّع ويُرفع حزمة SCORM، فخرجت الأسماء في اللقطات، وفي نصّ الشاشة تحت
كل صورة، وفي مسرد الحزمة التعليمية («Alhanouf Alsubaie»).

**الكشف تقديري ومكتوب على هذه الحدود:**

* يلتقط أسماء بالحروف اللاتينية كما تعرضها أنظمة المدارس: سطرٌ من
  كلمتين إلى أربع، كلٌّ منها تبدأ بحرف كبير، وليس فيها كلمة واجهة معروفة
  (``_UI_WORDS``) — و«Abdullah Omran» يُموَّه و«Math Education» يبقى.
* وعناوين البريد الإلكتروني.
* لا يلتقط الأسماء العربية المكتوبة على الشاشة، ولا الأسماء المنطوقة في
  الكلام — الثانية تعالجها تعليمة الصياغة (``ai/rewriter.py``).

والخطأ في اتجاه التمويه أرخص: كلمة واجهة مموَّهة لا تضرّ قارئًا،
واسمٌ مكشوف لا يُستردّ بعد التوزيع.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List

from utils.logger import logger

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NAME_WORD = re.compile(r"^[A-Z][A-Za-z'\-]{1,}$")
_LOWER_NAME_WORD = re.compile(r"^[a-z][a-z'\-]{2,}$")

#: كلمات واجهة وتعليم شائعة. أي سطر فيه واحدة منها ليس اسم شخص.
_UI_WORDS = {w.lower() for w in """
    Admin Account Activity All Arabic Art Assessment Assessments Attached Back
    Boys Biology Calendar Cancel Chemistry Class Classes Close Comment Comments
    Computer Criteria Curriculum Dashboard Day Days Delete Edit Education
    Elementary English Feedback File Filter French Friday Geography Girls Grade
    Group Groups Help High History Holy Home Instruction Islamic Kg Lesson
    Lessons Math Mathematics Menu Met Middle Monday Month Music New Next Not
    Physical Physics Plan Planned Planner Pending Primary Quran Reports Reset
    Resources Saturday School Schools Science Search Section Select Settings
    Share Shared Social Studies Subject Sunday Support Teacher Teachers This
    Thursday Timeline Today Tuesday Unit Units Upload View Wednesday Week Weekly
    Welcome With Your Assessments Activity Month Year Page Status Total Report
    January February March April May June July August September October
    November December Sep Oct Nov Dec Jan Feb Mar Apr Jun Jul Aug
    Summary Details Overview Profile Logout Login Sign Student Students Parent
    Parents Staff Principal Department Library Sports Health Technology
""".split()}


#: أسماء أوّل شائعة بالحرف اللاتيني كما تكتبها أنظمة المدارس في الخليج
#: ومصر. **دليلٌ إيجابي** على أن السطر اسم شخص — لا يكفي أن يكون كلمتين
#: بحرفٍ كبير. قيس على تسجيلٍ حقيقي: شرط «حرف كبير وليس كلمة واجهة»
#: وحده موّه قائمة تطبيقات PowerSchool كلّها («Performance Matters»،
#: «Schoology Learning»، «McGraw Hill»، «Local Disk») — أي الخطوة التي
#: يشرحها الفيديو نفسها — فخرج الدليل بقائمة مموّهة.
_FIRST_NAMES = {w.lower() for w in """
    Abdullah Abdulaziz Abdulrahman Abdulrahim Abdulkarim Abdelrahman Abrar
    Adel Adwaa Afnan Ahad Ahmad Ahmed Aisha Aishah Alaa Ali Amal Amani Amira
    Amjad Amna Amr Anas Anwar Arwa Asma Asmaa Ayman Aziz Bader Badr Bandar
    Bashayr Bashair Basma Bayan Dalal Dalia Dana Deema Deemah Diana Dina Duaa
    Eman Emad Faisal Fahad Fahd Fatima Fatimah Fatma Ghada Hadeel Hala Hamad
    Hamza Hanan Hani Haya Hayat Hessa Hind Huda Hussain Hussein Hassan Ibrahim
    Iman Jana Joud Jawaher Jamila Khaled Khalid Khadija Lama Lamya Latifa Layla
    Leena Lina Maha Mahmoud Majed Manal Mansour Marwa Maryam Mariam Mashael
    Mohammed Mohamed Muhammad Mohammad Mona Muna Mustafa Nada Nasser Nawaf
    Nawal Njoud Noura Nora Norah Nouf Nour Noor Omar Osama Rakan Rana Rania
    Rawan Razan Reem Reema Rehab Saad Saeed Saleh Salem Salma Salman Samar
    Samir Sara Sarah Saud Shahad Shaima Sultan Taghreed Tariq Turki Waad Wafa
    Waleed Walid Yasser Yousef Yosef Yousif Youssef Zainab Zeinab Ziad Hanouf
    Johara Ghadeer Wejdan Abeer Asia Alia Aliyah Reham Ruba Shatha Somaya
    Mohamad Mohamd Mohammad Muhammed Hany Hanaa Hana Hesham Hisham Ashraf
    Magdy Magdi Maged Majid Asser Tamer Sherif Karim Kareem Mostafa Moustafa
    Hazem Wael Ehab Essam Gamal Hossam Hussam Ramy Rami Sameh Tarek Eid
    Deyaa Diaa Atef Adham Medhat Refaat Osman Othman Yasmin Yasmine Shaimaa
    Heba Doaa Nermeen Nesma Rasha Randa Walaa Wafaa Eslam Ismail Yehia Yahya
    Zakaria Abdallah Abdelaziz Sayed Fathy Fathi Mohsen Mohsin Hamdy Hamdi
    Ragab Ramadan Shaaban Sabry Sabri Sobhy Ashour Nabil Nabeel Maher
    Mamdouh Hatem Helmy Galal Bassem Basem Hussien Hosny Kamal Lotfy Mounir
    Nader Naser Raafat Reda Sameer Samy Sami Shady Shadi Tawfik Zaki Nasr
    Hamada Esraa Israa Nahla Noha Nagwa Naglaa Hend Hadeer Hager Omnia Radwa
    Salwa Sanaa Nashwa Enas Inas Manar Menna Rahma Shereen Sherine Mohanad
    Muhannad Moaz Muath Obada Amgad Ayman Marwan Hesha Asham Shoubak
""".split()}

#: أسماء أوّل تُطابق كلمة واجهة أو جغرافيا — لا تكفي وحدها دليلًا حين
#: تظهر كلمةً منفردة (``_single_name_tokens``).
_AMBIGUOUS_SINGLE = {"asia", "dana", "hala", "nada", "amal", "jana", "mona",
                     "hayat", "bayan", "noor", "nour", "reem", "lama", "haya"}

#: اسم عرضٍ مقصوص بنقاط كما تعرضه بطاقات المشاركين في Teams/Zoom:
#: «Hanaa...»، «Mohame…». رُصد على تسجيل اجتماع حقيقي: 128 لقطة فيها
#: لوحة مشاركين، والأسماء المقصوصة نجت من التمويه لأن الكاشف يشترط
#: كلمتين كاملتين — وظهرت في تعليقات الصور («Mohame... Hesha»).
_TRUNCATED_NAME = re.compile(r"^[A-Z][A-Za-z'\-]{1,14}(?:\.{2,}|…)$")

#: وسومٌ يليها اسم شخص في أنظمة المدارس والتقييم («Signed by Mohamad
#: Ahmad on …»، «Current User» ثم الاسم في السطر التالي). الاسم هنا
#: قد يكون خارج قائمة الأسماء الأولى، والوسم وحده دليلٌ كافٍ.
_INLINE_NAME_LABELS = re.compile(
    r"^(?:by|(?:user|employee|evaluator|supervisor|teacher|name|observer|"
    r"reviewer|approver|owner|signature):)$", re.I)
_LINE_NAME_LABELS = re.compile(
    r"^(?:current user|submitted by|signed by|created by|modified by|"
    r"approved by|assigned to|assigned by|evaluator|supervisor|observer|"
    r"employee|employee name|teacher|teacher name|staff name|full name|"
    r"user name|name|reviewer|approver)\s*:?$", re.I)
#: بادئات الأنساب: Alsubaie، AlOtaibi، Elashry، Abunayyan، Bin، Abdel….
_SURNAME = re.compile(r"^(?:al|el|abu|bin|abd|ibn)[a-z]{3,}$", re.I)


#: كلمات إنجليزية تبدأ بالبادئات نفسها وليست أنسابًا.
_NOT_SURNAME = {w.lower() for w in """
    allow allowed allows also already almost alternate alternative although
    always altogether alert alerts algebra algorithm album alumni alphabet
    alphabetical alignment aligned else elsewhere element elements elective
    electives electric electronic eligible eligibility elapsed elementary
    binary binder binders ibnsina abduct
""".split()}


def looks_like_person_name(text: str) -> bool:
    """هل النصّ (عنوانٌ أو تعليقٌ مرشّح) اسمُ شخص أو يحمل اسمًا مقصوصًا؟"""
    tokens = [t.strip(".,:;()[]!|") for t in (text or "").split()]
    raw = (text or "").split()
    if any(_TRUNCATED_NAME.match(t) for t in raw):
        return True
    if any(_NAME_WORD.match(t) and t.lower() in _FIRST_NAMES
           and t.lower() not in _AMBIGUOUS_SINGLE for t in tokens):
        return True
    for size in (4, 3, 2):
        for index in range(0, max(0, len(tokens) - size + 1)):
            if _is_name_line(tokens[index:index + size]):
                return True
    return False


def _name_run(tokens: List[str], start: int) -> int:
    """طول سلسلة كلمات تشبه اسمًا تبدأ عند ``start`` (0 إن لم توجد)."""
    length = 0
    for token in tokens[start:start + 4]:
        if not (_NAME_WORD.match(token) and len(token) >= 2
                and token.lower() not in _UI_WORDS):
            break
        length += 1
    return length


def _is_person(words: List[str]) -> bool:
    return any(w.lower() in _FIRST_NAMES
               or (_SURNAME.match(w) and w.lower() not in _NOT_SURNAME)
               for w in words)


def _is_name_line(words: List[str]) -> bool:
    if not 2 <= len(words) <= 4:
        return False
    if any(w.lower() in _UI_WORDS for w in words):
        return False
    if not _is_person(words):
        return False
    # بحرفٍ كبير كما تكتبه أنظمة المعلّمين، أو صغيرٍ كلّه كما تعرض
    # PowerSchool SIS أسماء الطلاب («rashad abdelmohaymen abu alsaimi»)
    # — نجت هذه من الإخفاء لأن الشرط كان الحرف الكبير وحده.
    return all((_NAME_WORD.match(w) or _LOWER_NAME_WORD.match(w)) and len(w) >= 3
               for w in words)


def find_name_boxes(words: Iterable[dict]) -> tuple[list[dict], list[str]]:
    """‏(كلمات تُموَّه، الأسماء النصّية) من كلمات OCR بإحداثياتها."""
    lines: "OrderedDict[tuple, list[dict]]" = OrderedDict()
    for word in words:
        lines.setdefault(word["line"], []).append(word)

    hide: list[dict] = []
    names: list[str] = []
    for items in lines.values():
        # ‏«AlOtaib!»: الحرف الأخير قراءةٌ خاطئة لـ i/l — لا جزءٌ من الاسم
        tokens = [w["text"].strip(".,:;()[]!|") for w in items]
        for w, token in zip(items, tokens):
            if _EMAIL.fullmatch(token):
                hide.append(w)
                names.append(token)
        # نافذة منزلقة: «AA Alhanouf Alsubaie View Account» سطرٌ واحد
        index = 0
        while index < len(tokens):
            for size in (4, 3, 2):
                window = tokens[index:index + size]
                if len(window) == size and _is_name_line(window):
                    hide.extend(items[index:index + size])
                    names.append(" ".join(window))
                    index += size
                    break
            else:
                index += 1
    # عمود الأسماء يُستنتج من الأسماء الكاملة وحدها (السلوك المُعايَر)،
    # ثم تُضاف الأدلّة الأضعف: اسمٌ مقصوص، ووسمٌ يليه اسم، واسمٌ منفرد.
    hide.extend(_same_column_rows(lines, hide))
    hide.extend(_soft_name_words(lines, hide, names))
    hide.extend(_labelled_next_lines(lines, hide, names))
    unique: dict[int, dict] = {}
    for word in hide:
        unique.setdefault(id(word), word)
    return list(unique.values()), list(dict.fromkeys(names))


def _soft_name_words(lines, hidden: list[dict], names: list[str]) -> list[dict]:
    """أدلّة أضعف من السطر الكامل — انظر ``_TRUNCATED_NAME`` و``_INLINE_NAME_LABELS``."""
    hide: list[dict] = []
    hidden_here = {id(w) for w in hidden}
    for items in lines.values():
        tokens = [w["text"].strip(".,:;()[]!|") for w in items]

        def take(position: int, items: list[dict] = items) -> bool:
            if 0 <= position < len(items) and id(items[position]) not in hidden_here:
                hide.append(items[position])
                hidden_here.add(id(items[position]))
                return True
            return False

        for position, (w, token) in enumerate(zip(items, tokens)):
            raw_token = w["text"].strip()
            # اسم عرضٍ مقصوص («Hanaa...»): هو وجاره الملاصق إن كان كلمة
            # بحرفٍ كبير (الشطر الثاني من الاسم: «Mohame... Hesha»).
            if _TRUNCATED_NAME.match(raw_token):
                if take(position):
                    names.append(raw_token)
                for neighbour in (position - 1, position + 1):
                    if 0 <= neighbour < len(tokens):
                        other = tokens[neighbour]
                        if (_NAME_WORD.match(other) or len(other) <= 2) and \
                                other.lower() not in _UI_WORDS:
                            if take(neighbour) and len(other) >= 3:
                                names.append(other)
            # وسمٌ يليه اسم: «Signed by Mohamad Ahmad on …».
            elif _INLINE_NAME_LABELS.match(raw_token) and position + 1 < len(tokens):
                run = _name_run(tokens, position + 1)
                if run >= 2 or (run == 1 and token.lower().rstrip(":") != "by"):
                    taken = [take(k) for k in range(position + 1, position + 1 + run)]
                    if any(taken):
                        names.append(" ".join(tokens[position + 1:position + 1 + run]))
            # اسم أوّل معروف منفردًا («Signature: Maged»، «My Staff Hanaa»).
            elif (_NAME_WORD.match(token) and token.lower() in _FIRST_NAMES
                  and token.lower() not in _AMBIGUOUS_SINGLE):
                if take(position):
                    names.append(token)
    return hide


def _labelled_next_lines(lines, hidden: list[dict], names: list[str]) -> list[dict]:
    """السطر الذي يلي وسمًا قائمًا بذاته («Current User») اسمُ شخص."""
    hidden_ids = {id(w) for w in hidden}
    ordered = list(lines.values())
    extra: list[dict] = []
    for current, following in zip(ordered, ordered[1:]):
        label = " ".join(w["text"].strip() for w in current)
        if not _LINE_NAME_LABELS.match(label):
            continue
        tokens = [w["text"].strip(".,:;()[]!|") for w in following]
        run = _name_run(tokens, 0)
        if run == 0:
            continue
        for w in following[:run]:
            if id(w) not in hidden_ids:
                extra.append(w)
                hidden_ids.add(id(w))
        names.append(" ".join(tokens[:run]))
    return extra


#: فرق يسار الكلمة الأولى المقبول ليُعدّ السطر في عمود الأسماء نفسه.
COLUMN_TOLERANCE_PX = 12


def _same_column_rows(lines, hidden: list[dict]) -> list[dict]:
    """أسطرٌ في عمود قائمة الأسماء نفسه، قرأها OCR مشوَّهة.

    قائمة معلّمات حقيقية: «Abdullah Omran» و«Afnan Alharbi» كُشفا، و
    «Adwaa Alwaqdani» قُرئ «مديهة Alwaqdont» فنجا. السطر الذي يبدأ عند
    يسار اسمٍ مكشوف وارتفاعه مثله اسمٌ في القائمة نفسها غالبًا.
    """
    if not hidden:
        return []
    # العمود عمودُ أسماء إن بدأ فيه اسمان مكشوفان فأكثر. اسمٌ واحد
    # (المستخدم نفسه في الترويسة) لا يجعل ما تحته قائمة أشخاص — رُصد
    # تمويه نصوص منشورات المنصّة العربية تحت اسم المستخدم.
    # وسطرٌ فيه كلمة تشبه نسبًا («Alwaqdont») يكفيه اسمٌ واحد في عموده.
    lefts = [w["left"] for w in hidden]
    every = [(w["left"], w["height"]) for w in hidden]
    listed = [(w["left"], w["height"]) for w in hidden
              if sum(1 for x in lefts if abs(x - w["left"]) <= COLUMN_TOLERANCE_PX) >= 2]
    hidden_ids = {id(w) for w in hidden}
    extra: list[dict] = []
    for items in lines.values():
        if any(id(w) in hidden_ids for w in items):
            continue
        texts = [w for w in items if w["text"].strip("©®@|_-—")]
        if not texts:
            continue
        first = texts[0]
        cleaned = [w["text"].strip(".,:;()[]!|") for w in texts]
        anchors = every if _is_person(cleaned) else listed
        in_column = any(abs(first["left"] - left) <= COLUMN_TOLERANCE_PX
                        and 0.6 <= first["height"] / max(1.0, height) <= 1.6
                        for left, height in anchors)
        if not in_column:
            continue
        # سطرٌ بلا كلمةٍ تشبه اسمًا (أزمنة، أرقام، كلمات واجهة) ليس اسمًا:
        # «12:15 PM - 12:45 PM» في بطاقةٍ يوافق يسارُها يسارَ اسم مصادفةً.
        looks_named = any(
            (_NAME_WORD.match(t) and len(t) >= 3 and t.lower() not in _UI_WORDS)
            or re.search(r"[\u0600-\u06FF]{3,}", t)
            for t in cleaned)
        if not looks_named:
            continue
        for w, t in zip(texts, cleaned):
            if t.lower() not in _UI_WORDS:
                extra.append(w)
    return extra


def blur_boxes(image_path: Path, boxes: list[dict], pad: int = 3) -> bool:
    """يُبكسل المستطيلات في الصورة ويحفظها مكانها."""
    if not boxes:
        return False
    from PIL import Image

    with Image.open(image_path) as source:
        image = source.convert("RGB")
    for box in boxes:
        left = max(0, int(box["left"]) - pad)
        top = max(0, int(box["top"]) - pad)
        right = min(image.width, int(box["left"] + box["width"]) + pad)
        bottom = min(image.height, int(box["top"] + box["height"]) + pad)
        if right - left < 2 or bottom - top < 2:
            continue
        region = image.crop((left, top, right, bottom))
        small = region.resize((max(1, region.width // 8),
                               max(1, region.height // 8)))
        image.paste(small.resize(region.size, Image.NEAREST), (left, top))
    suffix = image_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        image.save(image_path, quality=88)
    else:
        image.save(image_path)
    return True


def scrub_person_terms(glossary: str) -> str:
    """يحذف من قائمة مصطلحات مفصولة بفواصل ما يبدو اسم شخص أو بريدًا.

    مصطلحات الشاشة المستخرجة آليًّا (``video/screen_terms``) تحمل أسماء
    المعلّمات كما تظهر في القوائم («Alhanouf Alsubaie»). تنفع التفريغ
    محلّيًّا، لكنها لا تُرسل إلى مزوّد سحابي حين يُطلب إخفاء الأسماء.
    """
    kept = []
    for term in re.split(r"[،,]", glossary or ""):
        term = " ".join(term.split())
        if not term or _EMAIL.search(term):
            continue
        if looks_like_person_name(term):
            continue
        words = term.split()
        if len(words) == 1 and words[0].lower() in _FIRST_NAMES:
            continue
        kept.append(term)
    return "، ".join(kept)


def scrub_text(text: str, names: Iterable[str]) -> str:
    """يحذف الأسماء المكتشفة وعناوين البريد من نصّ الشاشة."""
    # حدود كلمة لا استبدالٌ حرفي: الأسماء المنفردة («Ali») صارت تُكشف،
    # و``replace`` كان سيحذفها من داخل «Alignment».
    for name in sorted(set(names), key=len, reverse=True):
        if not name.strip():
            continue
        text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", "", text)
    text = _EMAIL.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if len(line) >= 2)


def redact_keyframe(image_path: Path, words: list[dict], text: str
                    ) -> tuple[str, list[str]]:
    """يموّه الأسماء في اللقطة ويعيد (النصّ منقّى، الأسماء)."""
    boxes, names = find_name_boxes(words)
    if boxes:
        try:
            blur_boxes(image_path, boxes)
        except Exception as exc:                     # noqa: BLE001
            logger.warning(f"تعذّر تمويه الأسماء في {image_path.name}: {exc}")
    return scrub_text(text, names), names
