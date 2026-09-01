class AppBaseException(Exception):
    """الاستثناء الجذر لكل أخطاء التطبيق."""


class MediaValidationError(AppBaseException):
    """ملف الفيديو مفقود أو تالف أو غير مدعوم."""


class FFmpegExecutionError(AppBaseException):
    """فشل تنفيذ أمر FFmpeg/FFprobe."""


class FFprobeNotFoundError(AppBaseException):
    """تعذر العثور على ffprobe على النظام أو ضمن حزمة التطبيق."""


class ResourceAllocationError(AppBaseException):
    """فشل تخصيص VRAM/RAM أو تحميل النموذج."""


class PipelineCancelledError(AppBaseException):
    """أُلغيت العملية بطلب المستخدم."""


class DocumentGenerationError(AppBaseException):
    """فشل توليد مستند Word."""


class ModelUnavailableError(AppBaseException):
    """نموذج التفريغ غير مثبّت ولم يُسمح بتنزيله."""


class InsufficientDiskSpaceError(AppBaseException):
    """المساحة الحرة لا تكفي لإتمام المهمة."""


class ArtifactMissingError(AppBaseException):
    """حالة المهمة تشير إلى ناتج غير موجود — عطل استئناف."""
