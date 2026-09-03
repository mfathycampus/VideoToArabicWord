# VideoToArabicWord 1.4.0 — P1 Quality Gate & Provenance

## الهدف
رفع جودة المخرجات من مجرد «ملف Word صالح» إلى «خطة محتوى قابلة للقياس والتتبع قبل التصيير».

## التغييرات
- إضافة `document/quality.py` لحساب Quality Report.
- قياس: اكتمال النص، تغطية الصور، تغطية OCR، جودة الأقسام، وتغطية provenance.
- منع تكرار `segment_id` و`image_id` عبر Quality Gate.
- حفظ `quality.json` مع كل خطة جديدة.
- إضافة `quality_score` و`quality_warnings` إلى `DocumentPlan` مع توافق رجعي.
- إضافة `source_start` و`source_end` إلى `DocumentBlock` لربط المحتوى بزمن المصدر.
- Planner يملأ provenance تلقائيًا للفقرات والأشكال.
- OCR يظل اختياريًا ولا يخفض جودة المستند وحده عند غيابه.

## بوابة النشر
لا يسمح الـpipeline بالانتقال إلى إنشاء DOCX إذا كانت الخطة تحتوي مراجع مكررة أو كانت الدرجة الإجمالية أقل من 85%.

## الاختبارات
مجموعة Quality/Planner/Document الحالية: **44 passed**.

الاختبارات الشاملة في الحزمة الحالية لا يمكن اعتبارها معيارًا كاملًا لأن `testdata/corpus` غير مرفق في النسخة المصدرية، كما أن `faster-whisper` غير مثبت في بيئة الفحص الحالية. هذه قيود بيئة الاختبار وليست نتيجة مباشرة لتعديلات P1.
