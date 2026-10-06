"""مزوّدو نماذج اللغة لإعادة الصياغة.

ADR-016: إعادة الصياغة **اختيارية ومعطّلة افتراضيًا**، ولا تُفعَّل إلا
بطلب صريح. هي الاستثناء المُعلَن الوحيد على ADR-002 (لا شبكة) عند اختيار
مزوّد سحابي، والمزوّد المحلي (Ollama) يبقي كل شيء على الجهاز.

ADR-005 محفوظ: النص الخام لا يُمسّ أبدًا. الصياغة طبقة موازية تُحفظ في
ملف مستقل، ويمكن دائمًا توليد المستند من الخام.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from core.exceptions import AppBaseException
from utils.logger import logger


class RewriteUnavailableError(AppBaseException):
    """المزوّد غير متاح أو غير مُهيّأ.

    ``retryable`` يفرّق بين خطأ عابر يستحق إعادة المحاولة (تحديد معدّل،
    عطل مؤقت في الخدمة، انقطاع شبكة) وخطأ نهائي لا تنفع معه (مفتاح
    خاطئ، نموذج غير متاح). بدون هذا التمييز كانت طبقة الصياغة تعامل
    ``429`` معاملة المفتاح الخاطئ فتُهدر كل الدفعات السابقة.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ResponseTruncatedError(RewriteUnavailableError):
    """الردّ قُطع عند سقف الرموز (``max_tokens``) قبل أن يكتمل.

    رُصد على تشغيل حقيقي (1.14.0، «تدريب بيرفورم»، 81 دقيقة): ردّ
    المؤلّف المرئي «ليس JSON»، و14 من 16 دفعة نصّية «بلا فقرات» —
    والدفعات الساقطة هي بالضبط ذات اللقطات الكثيرة. ‏JSON مقطوع في
    منتصفه لا يُحلَّل، وكان يُعامَل كردٍّ فارغ فيخرج النص الخام بصمت.

    الآن يُرفع صراحةً ومعه النصّ الجزئي، فيعيد المستدعي المحاولة بسقف
    أعلى أو بجزء أصغر بدل أن يستسلم.
    """

    def __init__(self, message: str, partial: str = "") -> None:
        super().__init__(message, retryable=False)
        self.partial = partial


# ---------------------------------------------------------------------
# حفظ الردود التي تعذّر استعمالها — للتشخيص بعد التشغيل
# ---------------------------------------------------------------------
# «ليس JSON بالشكل المطلوب» بلا الردّ نفسه لا يُشخَّص: هل قُطع؟ هل
# أحاطه النموذج بشرح؟ هل رفض؟ الـ pipeline يضبط مجلّد المهمة عند البدء،
# وكل ردّ لم يُستعمل يُحفظ فيه (على الجهاز فقط — لا يغادر شيء).

_DUMP: dict = {"dir": None, "count": 0}
MAX_DUMPS = 40


def set_response_dump_dir(path) -> None:
    """يُضبط من الـ pipeline لكل مهمّة؛ ``None`` يعطّل الحفظ."""
    from pathlib import Path as _Path

    _DUMP["dir"] = _Path(path) if path else None
    _DUMP["count"] = 0


def dump_failed_response(label: str, raw: str, reason: str = "") -> None:
    """يحفظ ردًّا لم يُستعمل في ``<مجلد المهمة>/ai_debug``. لا يرفع أبدًا."""
    directory = _DUMP.get("dir")
    if directory is None or _DUMP["count"] >= MAX_DUMPS:
        return
    try:
        import re as _re

        target = directory / "ai_debug"
        target.mkdir(parents=True, exist_ok=True)
        _DUMP["count"] += 1
        safe = _re.sub(r"[^\w\-]+", "_", label, flags=_re.UNICODE).strip("_")[:60]
        name = f"{_DUMP['count']:02d}_{safe or 'response'}.txt"
        (target / name).write_text(
            f"# السبب: {reason}\n# الطول: {len(raw or '')} حرفًا\n\n{raw or ''}",
            encoding="utf-8")
    except Exception as exc:                      # noqa: BLE001
        logger.debug(f"تعذّر حفظ الردّ للتشخيص: {exc}")


def _friendly_error(detail: str) -> str:
    """يستخرج رسالة الخطأ من رد JSON بدل عرض الرد الخام.

    عرض ``{"type":"error","error":{...}}`` كاملًا في نافذة للمستخدم
    يخفي الجملة المفيدة وسط تفاصيل تقنية.
    """
    try:
        payload = json.loads(detail)
        message = payload.get("error", {}).get("message")
        if message:
            return str(message)
    except Exception:
        pass
    return detail[:300]


# ---------------------------------------------------------------------
# سجلّ الخروج الفعلي — ما غادر الجهاز حقًّا، لا ما أذِن به الإعداد
# ---------------------------------------------------------------------
# ADR-020 · **الإعداد نيّة، والسجلّ شهادة.**
#
# كان ``audit.text_left_device`` يُحسب من الإعداد عند بدء المهمّة: مزوّد
# سحابيّ مُفعَّل ⇒ ``true``. ورُصد على مخرج حقيقي أن ذلك يكذب: الإعداد
# كان ``anthropic``، والمزوّد سقط قبل أن يُرسل شيئًا، وسطر التدقيق قال
# إن نصّ المحاضرة غادر الجهاز.
#
# وسجلٌّ يكذب لصالح التشدّد يفقد قيمته كدليل تمامًا كالذي يكذب لصالح
# التساهل — فمراجعُ الامتثال الذي يجد سطرًا واحدًا مخالفًا للواقع لا
# يثق بالملفّ كلّه.
#
# فالتسجيل الآن يقع **عند حدود الشبكة**: لحظة إرسال الطلب فعلًا، ومن
# داخل المزوّد نفسه. ولا سبيل إلى ``complete`` سحابيّ لا يمرّ بها.

_EGRESS: dict = {"sent": False, "providers": [], "attempts": [], "images": 0}


def reset_egress() -> None:
    """يُصفّر السجلّ عند بدء مهمّة — يستدعيه الـ pipeline."""
    _EGRESS["sent"] = False
    _EGRESS["providers"] = []
    _EGRESS["attempts"] = []
    _EGRESS["images"] = 0


def record_egress(provider: str, sent: bool, detail: str = "",
                  images: int = 0) -> None:
    """يسجّل محاولة إرسال. ``sent`` = غادرت البيانات فعلًا.

    ``images`` عدد لقطات الشاشة في الطلب (الوضع المرئي). تُعدّ منفصلةً
    عن النصّ لأنها قد تحمل ما لا يحمله التفريغ: أسماءً وأرقامًا ظاهرة.
    """
    name = str(provider or "?").strip().lower()
    _EGRESS["attempts"].append(
        {"provider": name, "sent": bool(sent), "detail": detail[:160],
         "images": int(images)})
    if sent:
        _EGRESS["sent"] = True
        _EGRESS["images"] += int(images)
        if name not in _EGRESS["providers"]:
            _EGRESS["providers"].append(name)


def _body_images(body: dict) -> int:
    """عدد الصور في جسم طلب Messages أو Chat Completions."""
    total = 0
    for message in body.get("messages", []) or []:
        content = message.get("content")
        if isinstance(content, list):
            total += sum(1 for part in content
                         if isinstance(part, dict)
                         and part.get("type") in ("image", "image_url"))
    return total


def egress_report() -> dict:
    """نسخة من السجلّ. ``sent=False`` مع محاولات = حاول ولم يُرسل."""
    return {
        "sent": _EGRESS["sent"],
        "providers": list(_EGRESS["providers"]),
        "attempts": list(_EGRESS["attempts"]),
        "images": int(_EGRESS["images"]),
    }


#: إعادة المحاولة على الأخطاء العابرة وحدها (تحديد معدّل، عطل خدمة،
#: انقطاع شبكة). والتراجع أُسّي كي لا نُغرق خدمةً تشتكي أصلًا.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 3.0


# ---------------------------------------------------------------------
# المحتوى متعدّد الوسائط — نصٌّ وصور في رسالة واحدة
# ---------------------------------------------------------------------
# ``user_prompt`` في ``complete`` نصٌّ عادةً. المؤلّف المرئيّ
# (``ai/visual_author``) يمرّر بدلًا منه **قائمة أجزاء**:
#     {"type": "text", "text": "..."}
#     {"type": "image", "media_type": "image/jpeg", "data": "<base64>"}
# وكل مزوّد يحوّلها إلى صيغة واجهته. مزوّدٌ لا يدعم الصور يرفض القائمة
# صراحةً (``supports_vision``) بدل أن يُسقط الصور بصمت فيكتب النموذج
# دليلًا عن شاشات لم يرها.

def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(data_b64: str, media_type: str = "image/jpeg") -> dict:
    return {"type": "image", "media_type": media_type, "data": data_b64}


def _is_parts(user_prompt) -> bool:
    return isinstance(user_prompt, list)


def _parts_text(parts: list) -> str:
    """النصّ وحده من قائمة أجزاء — لمزوّدٍ يستقبل الصور في حقل مستقل."""
    return "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _parts_images(parts: list) -> list:
    return [p["data"] for p in parts if p.get("type") == "image"]


def count_images(user_prompt) -> int:
    return len(_parts_images(user_prompt)) if _is_parts(user_prompt) else 0

#: نماذج Ollama التي تقرأ الصور. البقيّة نصّية، وإرسال صورٍ إليها يُهمَل
#: بصمت من جهة الخادم — أسوأ من الرفض الصريح.
_OLLAMA_VISION_HINTS = ("vl", "llava", "vision", "gemma3", "minicpm-v",
                        "moondream", "bakllava", "llama4", "mistral-small3")


def complete_with_retry(provider: "LLMProvider", system_prompt: str,
                        user_prompt: str, max_tokens: int = 2048,
                        timeout: int = 180) -> str:
    """نداء المزوّد مع إعادة محاولة للأخطاء العابرة وحدها.

    كان هذا المنطق حبيس ``TranscriptRewriter``، فلمّا احتاجته وحدةٌ
    ثانية (``ai/study_builder``) كان البديل نسخَه — أي منطقَ إعادة
    محاولة له نسختان تتباعدان عند أول تعديل. رُفع هنا ليُستعمل مرّة
    واحدة، والرِّوَيتر يستدعيه كما تستدعيه الحزمة التعليمية.
    """
    import time

    last: Optional[BaseException] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return provider.complete(system_prompt, user_prompt,
                                     max_tokens=max_tokens, timeout=timeout)
        except RewriteUnavailableError as exc:
            last = exc
            if not getattr(exc, "retryable", False):
                raise
            if attempt == MAX_ATTEMPTS:
                break
            delay = BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                f"خطأ عابر من المزوّد ({exc}) — "
                f"إعادة المحاولة {attempt}/{MAX_ATTEMPTS - 1} "
                f"بعد {delay:.0f} ثانية.")
            time.sleep(delay)
    raise last  # type: ignore[misc]


@dataclass
class ProviderInfo:
    name: str
    label_ar: str
    is_local: bool
    privacy_note: str


class LLMProvider(ABC):
    info: ProviderInfo
    #: هل يقرأ المزوّد صورًا ضمن الرسالة؟ انظر ``image_part``.
    supports_vision: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """فحص جاهزية بلا استهلاك — لا يُرسل النص."""

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2048, timeout: int = 180) -> str:
        ...


# ---------------------------------------------------------------------
class OllamaProvider(LLMProvider):
    """نموذج محلي عبر Ollama — لا يغادر النص الجهاز إطلاقًا."""

    info = ProviderInfo(
        name="ollama", label_ar="Ollama (محلي على جهازك)", is_local=True,
        privacy_note="النص لا يغادر جهازك إطلاقًا.")

    def __init__(self, model: str = "qwen2.5:7b-instruct",
                 host: str = "http://127.0.0.1:11434") -> None:
        self.model = model
        self.host = host.rstrip("/")

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        name = (self.model or "").lower()
        return any(hint in name for hint in _OLLAMA_VISION_HINTS)

    def is_available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=4) as r:
                data = json.loads(r.read())
            names = {m.get("name", "") for m in data.get("models", [])}
            base = self.model.split(":")[0]
            return any(n == self.model or n.startswith(base) for n in names)
        except Exception:
            return False

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2048, timeout: int = 180) -> str:
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system_prompt},
                self._user_message(user_prompt),
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            text = data.get("message", {}).get("content", "")
            if data.get("done_reason") == "length":
                raise ResponseTruncatedError(
                    f"قُطع ردّ Ollama عند سقف {max_tokens} رمزًا.", partial=text)
            return text
        except urllib.error.URLError as exc:
            raise RewriteUnavailableError(
                f"تعذر الوصول إلى Ollama: {exc}", retryable=True) from exc

    def _user_message(self, user_prompt) -> dict:
        if not _is_parts(user_prompt):
            return {"role": "user", "content": user_prompt}
        images = _parts_images(user_prompt)
        if images and not self.supports_vision:
            raise RewriteUnavailableError(
                f"النموذج «{self.model}» لا يقرأ الصور. اختر نموذجًا مرئيًّا "
                "(مثل qwen2.5vl) أو عطّل «إرسال لقطات الشاشة».")
        message = {"role": "user", "content": _parts_text(user_prompt)}
        if images:
            message["images"] = images
        return message


class OpenAICompatibleProvider(LLMProvider):
    """أي خدمة تتبع واجهة OpenAI (OpenAI، Groq، Together، خادم محلي…).

    ⚠ سحابي: النص **يُرسل إلى خادم خارجي**. يتطلب موافقة صريحة.
    """

    info = ProviderInfo(
        name="openai_compatible", label_ar="خدمة سحابية (واجهة OpenAI)",
        is_local=False,
        privacy_note="⚠ يُرسل نص التفريغ إلى خادم خارجي.")

    #: نماذج الواجهة الحديثة تقرأ الصور (‏gpt-4o وما بعده). خادمٌ متوافق
    #: لا يدعمها يردّ بخطأ صريح يصل إلى المستخدم.
    supports_vision = True

    def __init__(self, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1",
                 api_key_env: str = "OPENAI_API_KEY",
                 api_key: str = "") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self._explicit_key = api_key.strip()

    @property
    def api_key(self) -> Optional[str]:
        return self._explicit_key or os.environ.get(self.api_key_env)

    def is_available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2048, timeout: int = 180) -> str:
        if not self.api_key:
            raise RewriteUnavailableError(
                f"مفتاح الوصول غير مضبوط. عيّن متغيّر البيئة {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._user_content(user_prompt)},
            ],
        }).encode("utf-8")
        images = count_images(user_prompt)
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                # الاتصال قام والردّ وصل ⇒ النصّ غادر الجهاز يقينًا.
                record_egress(self.info.name, True, images=images)
                data = json.loads(response.read())
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            if choice.get("finish_reason") == "length":
                raise ResponseTruncatedError(
                    f"قُطع الردّ عند سقف {max_tokens} رمزًا.", partial=text)
            return text
        except urllib.error.HTTPError as exc:
            # ردٌّ بخطأ يعني أن الطلب **وصل** — أي أن النصّ غادر.
            record_egress(self.info.name, True, f"HTTP {exc.code}", images=images)
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RewriteUnavailableError(
                f"رفضت الخدمة الطلب ({exc.code}): {detail}",
                retryable=exc.code == 429 or exc.code >= 500) from exc
        except urllib.error.URLError as exc:
            # لم يُفتح اتصال: لم يغادر شيء.
            record_egress(self.info.name, False, str(exc))
            raise RewriteUnavailableError(
                f"تعذر الاتصال بالخدمة: {exc}", retryable=True) from exc

    @staticmethod
    def _user_content(user_prompt):
        if not _is_parts(user_prompt):
            return user_prompt
        content = []
        for part in user_prompt:
            if part.get("type") == "image":
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{part.get('media_type', 'image/jpeg')};"
                           f"base64,{part['data']}"}})
            else:
                content.append({"type": "text", "text": part.get("text", "")})
        return content


# المزوّدون المتاحون بالترتيب المعروض في الواجهة
PROVIDER_CHOICES = (
    ("anthropic", "Claude API — أدق صياغة عربية"),
    ("maeen_managed", "معين المُدار — بلا مفتاح (رصيد دقائق)"),
    ("ollama", "Ollama — نموذج محلي على جهازك"),
    ("openai_compatible", "خدمة أخرى بواجهة OpenAI"),
)


def build_provider(name: str, model: str = "", base_url: str = "",
                   api_key_env: str = "", api_key: str = "",
                   workspace_id: str = "") -> LLMProvider:
    if name == "anthropic":
        return AnthropicProvider(model=model, base_url=base_url,
                                 api_key_env=api_key_env or "ANTHROPIC_API_KEY",
                                 api_key=api_key, workspace_id=workspace_id)
    if name == "maeen_managed":
        return MaeenManagedProvider()
    if name == "ollama":
        return OllamaProvider(model=model or "qwen2.5:7b-instruct",
                              host=base_url or "http://127.0.0.1:11434")
    if name in ("openai", "openai_compatible"):
        return OpenAICompatibleProvider(
            model=model or "gpt-4o-mini",
            base_url=base_url or "https://api.openai.com/v1",
            api_key_env=api_key_env or "OPENAI_API_KEY",
            api_key=api_key)
    raise RewriteUnavailableError(f"مزوّد غير معروف: {name}")


class AnthropicProvider(LLMProvider):
    """واجهة Claude الرسمية (Messages API).

    شكل الطلب يختلف عن OpenAI: الترويسة ``x-api-key`` لا ``Authorization``،
    و ``system`` حقل مستقل لا رسالة في القائمة، والرد في ``content[0].text``.
    لذلك لا يصلح استخدام المزوّد المتوافق مع OpenAI معه.

    ⚠ سحابي: نص التفريغ يُرسل إلى خوادم Anthropic، ومعه لقطات الشاشة
    المختارة حين يُفعَّل الوضع المرئي (``rewrite.send_images``). الفيديو
    والصوت لا يُرسلان إطلاقًا.
    """

    API_VERSION = "2023-06-01"
    DEFAULT_MODEL = "claude-sonnet-5"
    supports_vision = True

    info = ProviderInfo(
        name="anthropic", label_ar="Claude API (Anthropic)", is_local=False,
        privacy_note="⚠ يُرسل نص التفريغ — ولقطات الشاشة إن فُعّل الوضع "
                     "المرئي — إلى خوادم Anthropic. الفيديو والصوت لا يُرسلان.")

    WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"

    # وسائط اختيارية قد ترفضها نماذج أحدث. تُرسَل فقط عند الطلب، وتُسقَط
    # تلقائيًا وتُعاد المحاولة إن رفضتها الخدمة.
    OPTIONAL_PARAMS = ("temperature", "top_p", "top_k")

    def __init__(self, model: str = "", base_url: str = "",
                 api_key_env: str = "ANTHROPIC_API_KEY",
                 api_key: str = "", workspace_id: str = "",
                 temperature: Optional[float] = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key_env = api_key_env or "ANTHROPIC_API_KEY"
        self._explicit_key = api_key.strip()
        self._workspace_id = workspace_id.strip()
        # الافتراضي: لا نرسل temperature إطلاقًا.
        # النماذج الأحدث (claude-sonnet-5 وما بعده) ترفضها بـ 400
        # «`temperature` is deprecated for this model». وقيمتها
        # الافتراضية لدى الخدمة مناسبة لمهمة التحرير أصلًا.
        self.temperature = temperature

    @property
    def workspace_id(self) -> str:
        """معرّف مساحة العمل — إلزامي للمفاتيح المرتبطة بهوية.

        مفاتيح Anthropic نوعان: مفتاح مساحة عمل يعمل مباشرةً، ومفتاح
        مرتبط بهوية (identity-linked) يرفض الطلب بـ 400 ما لم تُرسل معه
        ترويسة ``anthropic-workspace-id``.
        """
        return self._workspace_id or os.environ.get(self.WORKSPACE_ENV, "")

    @property
    def api_key(self) -> Optional[str]:
        """المفتاح من الإعداد أولًا ثم من متغيّر البيئة.

        تقديم الإعداد يجنّب أشهر التباس: ``setx`` لا يؤثر على البرامج
        المفتوحة، فيضبط المستخدم المفتاح ويظل يرى «غير مهيّأ».
        """
        return self._explicit_key or os.environ.get(self.api_key_env)

    def is_available(self) -> bool:
        """وجود المفتاح فقط — لا يُستهلك رصيد ولا يُرسل نص."""
        return bool(self.api_key)

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2048, timeout: int = 180) -> str:
        if not self.api_key:
            raise RewriteUnavailableError(self._missing_key_message())

        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,          # حقل مستقل، لا رسالة
            "messages": [{"role": "user",
                          "content": self._user_content(user_prompt)}],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature

        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
        }
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id
        headers.update(self._extra_headers())

        try:
            data = self._post(body, headers, timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            custom = self._translate_http_error(exc.code, detail)
            if custom is not None:
                raise custom from exc

            # وسيط مهمَل: أسقطه وأعد المحاولة مرة واحدة بدل الفشل.
            # يجعل المحرّك يعمل مع النماذج الحالية والقديمة معًا دون
            # أن يضطر المستخدم لتعديل إعداد.
            dropped = [p for p in self.OPTIONAL_PARAMS
                       if p in body and f"`{p}`" in detail]
            if exc.code == 400 and dropped and "deprecated" in detail.lower():
                for name in dropped:
                    body.pop(name, None)
                logger.info(
                    f"أسقطنا وسائط مهمَلة ({', '.join(dropped)}) وأعدنا المحاولة.")
                try:
                    data = self._post(body, headers, timeout)
                    return self._extract_text(data)
                except urllib.error.HTTPError as retry_exc:
                    detail = retry_exc.read().decode("utf-8", "replace")[:400]
                    exc = retry_exc

            if exc.code == 401:
                # مفاتيح Anthropic لها تاريخ انتهاء اختياري، ومفتاح
                # منتهٍ يرفض بنفس الرمز — فنذكر الاحتمالين.
                raise RewriteUnavailableError(
                    "رفضت Anthropic المفتاح (401).\n\n"
                    "الأسباب المحتملة:\n"
                    "  • المفتاح غير صحيح أو نُسخ ناقصًا\n"
                    "  • انتهت صلاحيته (تحقق من عمود Expires في Console)\n"
                    "  • أُلغي المفتاح\n\n"
                    "أنشئ مفتاحًا جديدًا وألصقه في حقل «مفتاح الوصول».") from exc
            if exc.code == 429:
                raise RewriteUnavailableError(
                    "تجاوزت حد الاستخدام لدى Anthropic (429). "
                    "انتظر قليلًا أو استخدم النموذج المحلي.",
                    retryable=True) from exc
            if exc.code >= 500:
                raise RewriteUnavailableError(
                    f"عطل مؤقت لدى Anthropic ({exc.code}).",
                    retryable=True) from exc
            if exc.code == 404:
                raise RewriteUnavailableError(
                    f"النموذج «{self.model}» غير متاح لحسابك (404). "
                    "جرّب claude-sonnet-5 أو غيّره من الإعدادات.") from exc
            if exc.code == 400 and "workspace" in detail.lower():
                # مفتاح مرتبط بهوية: يحتاج معرّف مساحة العمل صراحةً
                raise RewriteUnavailableError(
                    "مفتاحك مرتبط بهوية (identity-linked)، ويحتاج معرّف "
                    "مساحة العمل.\n\n"
                    "افتح Claude Console ← Settings ← Workspaces، وانسخ "
                    "معرّف مساحة العمل (يبدأ بـ wrkspc_)، وألصقه في حقل "
                    "«معرّف مساحة العمل».\n\n"
                    "أو أنشئ مفتاحًا تابعًا لمساحة عمل محددة بدل مفتاح "
                    "الهوية، فلا يحتاج المعرّف.") from exc
            raise RewriteUnavailableError(
                f"رفضت Anthropic الطلب ({exc.code}).\n\n"
                f"{_friendly_error(detail)}") from exc
        except urllib.error.URLError as exc:
            raise RewriteUnavailableError(
                f"تعذر الاتصال بـ Anthropic: {exc}", retryable=True) from exc

        return self._extract_text(data)

    # ------------------------------------------------------------------
    # خطّافات للمزوّد المُدار (``MaeenManagedProvider``): الأصل لا يغيّر سلوكه.
    def _missing_key_message(self) -> str:
        return (f"مفتاح Claude غير مضبوط. عيّن متغيّر البيئة {self.api_key_env}.\n"
                "في PowerShell:  setx ANTHROPIC_API_KEY \"sk-ant-...\"\n"
                "ثم أعد فتح البرنامج.")

    def _extra_headers(self) -> dict:
        return {}

    def _on_response_headers(self, headers) -> None:
        return None

    def _translate_http_error(self, code: int, detail: str):
        return None

    # ------------------------------------------------------------------
    def _post(self, body: dict, headers: dict, timeout: int) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                record_egress(self.info.name, True, images=_body_images(body))
                self._on_response_headers(response.headers)
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # ردٌّ بخطأ يعني أن الطلب وصل — النصّ غادر ولو رُفض.
            record_egress(self.info.name, True, f"HTTP {exc.code}",
                          images=_body_images(body))
            raise
        except urllib.error.URLError as exc:
            record_egress(self.info.name, False, str(exc))
            raise

    @staticmethod
    def _user_content(user_prompt):
        if not _is_parts(user_prompt):
            return user_prompt
        content = []
        for part in user_prompt:
            if part.get("type") == "image":
                content.append({"type": "image", "source": {
                    "type": "base64",
                    "media_type": part.get("media_type", "image/jpeg"),
                    "data": part["data"]}})
            else:
                content.append({"type": "text", "text": part.get("text", "")})
        return content

    @staticmethod
    def _extract_text(data: dict) -> str:
        text = "".join(block.get("text", "") for block in data.get("content", [])
                       if block.get("type") == "text")
        if data.get("stop_reason") == "max_tokens":
            # JSON مقطوع في منتصفه لا يُحلَّل — انظر ``ResponseTruncatedError``.
            limit = (data.get("usage") or {}).get("output_tokens", "?")
            raise ResponseTruncatedError(
                f"قُطع ردّ Claude عند سقف الرموز ({limit} رمزًا).", partial=text)
        return text


#: آخر رصيد معلوم (دقائق) وزمن التقاطه — تقرؤه الواجهة دون اتصال إضافي.
_managed_credit: dict = {}


def managed_credit_state() -> dict:
    """``{"minutes": float, "at": epoch}`` أو ``{}`` إن لم يصل رصيدٌ بعد."""
    return dict(_managed_credit)


class MaeenManagedProvider(AnthropicProvider):
    """«معين المُدار»: Claude بلا مفتاح — الدفع برصيد دقائق عند المزوّد.

    التفريغ واللقطات تجري على الجهاز كما هي. ما يتغيّر أن نداء Claude يمرّ
    عبر خادم التفعيل نفسه ويحمل **كود التفعيل** بدل المفتاح ومعه معرّف
    الجهاز. الخادم يتحقق من الكود والجهاز والرصيد، ويفرض النموذج، ثم
    يمرّر الطلب بمفتاحه ويخصم الاستهلاك الفعلي. لا يحفظ المحتوى.

    ``model`` و``api_key`` و``workspace_id`` من الإعداد تُتجاهل عمدًا: لا
    مفتاح عند العميل، والنموذج يقرّره الخادم.
    """

    info = ProviderInfo(
        name="maeen_managed", label_ar="معين المُدار (بلا مفتاح)",
        is_local=False,
        privacy_note="⚠ يُرسل نص التفريغ — ولقطات الشاشة إن فُعّل الوضع "
                     "المرئي — إلى خادم معين ثم إلى Claude. الفيديو والصوت "
                     "لا يُرسلان، ولا يحفظ الخادم المحتوى.")

    def __init__(self, *args, **kwargs) -> None:
        from licensing.client import server_url

        super().__init__(base_url="https://invalid.local")
        # الأصل يستبدل العنوان الفارغ بعنوان Anthropic — وهذا هنا تسريبٌ
        # للكود. فنضبطه بعد الإنشاء، وبلا خادم مضبوط لا مفتاح ولا إرسال.
        self.base_url = server_url()
        self.model = ""                    # يقرّره الخادم

    @property
    def workspace_id(self) -> str:         # لا معنى له عند الخادم المُدار
        return ""

    @property
    def api_key(self) -> Optional[str]:
        from licensing import app_gate

        if not self.base_url:
            return None
        return app_gate.current_code()

    def _missing_key_message(self) -> str:
        return ("لا يوجد كود فعّال من باقة «مُدار». فعّل الكود من نافذة "
                "التفعيل، أو اختر «Claude API» واستخدم مفتاحك.")

    def _extra_headers(self) -> dict:
        from licensing import app_gate

        return {"x-device": app_gate.device_id()}

    def _on_response_headers(self, headers) -> None:
        """يلتقط الرصيد المتبقي الذي يرسله الخادم مع كل ردّ."""
        try:
            minutes = float(headers.get("x-maeen-credit-minutes"))
        except (TypeError, ValueError):
            return
        _managed_credit.update(minutes=minutes, at=time.time())
        logger.info(f"الرصيد المتبقي: {minutes:g} دقيقة")

    def _translate_http_error(self, code: int, detail: str):
        # رسائل الخادم عربية ومفهومة (نفد الرصيد، الجهاز، الإلغاء…).
        # 400 يبقى للمعالجة العامة. الرصيد (402) لا يُعاد بلا شحن.
        if code == 400:
            return None
        if code >= 400:
            return RewriteUnavailableError(
                _friendly_error(detail),
                retryable=code == 429 or code >= 500)
        return None


def provider_from_settings(settings) -> "LLMProvider":
    """المزوّد من قسم إعداد (rewrite أو study) — نقطة واحدة بدل ثلاث نسخ.

    كانت ``core/pipeline`` تبني المزوّد من الحقول الستّة نفسها في ثلاثة
    مواضع؛ حقلٌ يُضاف لأحدها (``workspace_id`` مثلًا) يُنسى في الآخرَين.
    """
    return build_provider(
        settings.provider, model=settings.model,
        base_url=settings.base_url, api_key_env=settings.api_key_env,
        api_key=settings.api_key, workspace_id=settings.workspace_id)
