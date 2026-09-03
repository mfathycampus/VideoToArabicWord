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


@dataclass
class ProviderInfo:
    name: str
    label_ar: str
    is_local: bool
    privacy_note: str


class LLMProvider(ABC):
    info: ProviderInfo

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
                {"role": "user", "content": user_prompt},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            return data.get("message", {}).get("content", "")
        except urllib.error.URLError as exc:
            raise RewriteUnavailableError(
                f"تعذر الوصول إلى Ollama: {exc}", retryable=True) from exc


class OpenAICompatibleProvider(LLMProvider):
    """أي خدمة تتبع واجهة OpenAI (OpenAI، Groq، Together، خادم محلي…).

    ⚠ سحابي: النص **يُرسل إلى خادم خارجي**. يتطلب موافقة صريحة.
    """

    info = ProviderInfo(
        name="openai_compatible", label_ar="خدمة سحابية (واجهة OpenAI)",
        is_local=False,
        privacy_note="⚠ يُرسل نص التفريغ إلى خادم خارجي.")

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
                {"role": "user", "content": user_prompt},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RewriteUnavailableError(
                f"رفضت الخدمة الطلب ({exc.code}): {detail}",
                retryable=exc.code == 429 or exc.code >= 500) from exc
        except urllib.error.URLError as exc:
            raise RewriteUnavailableError(
                f"تعذر الاتصال بالخدمة: {exc}", retryable=True) from exc


# المزوّدون المتاحون بالترتيب المعروض في الواجهة
PROVIDER_CHOICES = (
    ("anthropic", "Claude API — أدق صياغة عربية"),
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

    ⚠ سحابي: نص التفريغ يُرسل إلى خوادم Anthropic. الفيديو والصوت والصور
    لا تُرسل إطلاقًا.
    """

    API_VERSION = "2023-06-01"
    DEFAULT_MODEL = "claude-sonnet-5"

    info = ProviderInfo(
        name="anthropic", label_ar="Claude API (Anthropic)", is_local=False,
        privacy_note="⚠ يُرسل نص التفريغ إلى خوادم Anthropic (النص فقط).")

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
            raise RewriteUnavailableError(
                f"مفتاح Claude غير مضبوط. عيّن متغيّر البيئة {self.api_key_env}.\n"
                "في PowerShell:  setx ANTHROPIC_API_KEY \"sk-ant-...\"\n"
                "ثم أعد فتح البرنامج.")

        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,          # حقل مستقل، لا رسالة
            "messages": [{"role": "user", "content": user_prompt}],
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

        try:
            data = self._post(body, headers, timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]

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
    def _post(self, body: dict, headers: dict, timeout: int) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(body).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    @staticmethod
    def _extract_text(data: dict) -> str:
        return "".join(block.get("text", "") for block in data.get("content", [])
                       if block.get("type") == "text")
