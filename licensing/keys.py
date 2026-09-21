"""المفتاح العام المضمَّن — يُولَّد مرّة بـ ``python tools/make_license.py init``.

يبقى فارغًا في المستودع عمدًا: مفتاحٌ عامّ في الشيفرة لا ضرر فيه، لكن
تركه فارغًا يجعل نسخة المطوّر **غير موقَّعة**، فلا يُسلَّم ملفٌّ تنفيذي
بلا ترخيص بالخطأ. وأداة الإصدار تكتبه هنا عند التوليد.
"""

PUBLIC_KEY_B64 = "QXOoLWm0hPkfkSX6oL4E6ry96XblWlNIL0NabFXQMOI="

#: عنوان خادم التفعيل (Cloudflare Worker). يكتبه ``make_license.py init``
#: أو يُضبط بمتغيّر البيئة ``VTAW_LICENSE_SERVER`` أثناء التطوير.
SERVER_URL = "https://vtaw-license.mfathyy.workers.dev"
