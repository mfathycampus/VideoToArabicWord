class AppBaseException(Exception):
    """الاستثناء الجذر لكل أخطاء التطبيق."""

    error_code = "E0000"
    category = "INTERNAL_ERROR"

    def user_message(self) -> str:
        return str(self)


class UserInputError(AppBaseException):
    error_code = "E1000"
    category = "USER_ERROR"


class EnvironmentError(AppBaseException):
    error_code = "E2000"
    category = "ENVIRONMENT_ERROR"


class MediaError(AppBaseException):
    error_code = "E3000"
    category = "MEDIA_ERROR"


class ResourceError(AppBaseException):
    error_code = "E4000"
    category = "RESOURCE_ERROR"


class NetworkError(AppBaseException):
    error_code = "E5000"
    category = "NETWORK_ERROR"


class ModelError(AppBaseException):
    error_code = "E6000"
    category = "MODEL_ERROR"


class MediaValidationError(MediaError):
    error_code = "E3001"
    """ملف الفيديو مفقود أو تالف أو غير مدعوم."""


class FFmpegExecutionError(MediaError):
    error_code = "E3002"
    """فشل تنفيذ أمر FFmpeg/FFprobe."""


class FFprobeNotFoundError(EnvironmentError):
    error_code = "E2001"
    """تعذر العثور على ffprobe على النظام أو ضمن حزمة التطبيق."""


class ResourceAllocationError(ResourceError):
    error_code = "E4001"
    """فشل تخصيص VRAM/RAM أو تحميل النموذج."""


class PipelineCancelledError(AppBaseException):
    error_code = "E7001"
    category = "CANCELLATION"
    """أُلغيت العملية بطلب المستخدم."""


class DocumentGenerationError(AppBaseException):
    error_code = "E8001"
    category = "DOCUMENT_ERROR"
    """فشل توليد مستند Word."""


class QualityGateError(DocumentGenerationError, AssertionError):
    error_code = "E8002"
    """فشلت خطة المستند في اجتياز بوابة الجودة قبل التصيير (راجع quality.json)."""


class ModelUnavailableError(ModelError):
    error_code = "E6001"
    """نموذج التفريغ غير مثبّت ولم يُسمح بتنزيله."""


class InsufficientDiskSpaceError(ResourceError):
    error_code = "E4002"
    """المساحة الحرة لا تكفي لإتمام المهمة."""


class ArtifactMissingError(AppBaseException):
    error_code = "E9001"
    category = "STATE_ERROR"
    """حالة المهمة تشير إلى ناتج غير موجود — عطل استئناف."""
