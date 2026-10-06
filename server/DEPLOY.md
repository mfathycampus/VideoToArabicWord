# نشر الخادم والخدمة المُدارة

من PowerShell، في مجلد المشروع:

```powershell
cd server
npx wrangler login          # مرة واحدة (يفتح المتصفح)
npx wrangler deploy         # ينشر الكود والمحفظة (migration v1)
cd ..
python tools\push_managed_key.py --dry-run   # يعرض المفتاح مقنَّعًا بلا رفع
python tools\push_managed_key.py             # يرفعه سرًّا إلى Cloudflare
```

بعد النشر افتح `/admin`: يظهر شريط «الخدمة المُدارة: تعمل» وبطاقة التسعير.

## تجربة أولى
1. أصدر كودًا: الباقة **مُدار – فرد**، الرصيد 300 دقيقة.
2. في التطبيق: فعّل الكود، ثم اختر المزوّد «معين المُدار».
3. عالج فيديو قصيرًا معروف المدّة.
4. في `/admin`: ترى النداءات والرموز والتكلفة، وزر **معايرة** يحسب
   «نقطة لكل دقيقة» من الاستهلاك الفعلي.

## الإعدادات (wrangler.toml → [vars])
| المتغيّر | المعنى |
| --- | --- |
| `MANAGED_MODEL` | النموذج المفروض من الخادم |
| `UNITS_PER_MINUTE` | نقطة لكل دقيقة (الافتراضي؛ يُغيَّر حيًّا من الصفحة) |
| `MAX_OUTPUT_TOKENS` | سقف الخرج للنداء |
| `MAX_CONCURRENT` | نداءات متزامنة لكل كود |

الأسعار وسعر الدولار تُضبط من صفحة الإدارة (بطاقة التسعير).
الأسرار: `ANTHROPIC_API_KEY` و`ANTHROPIC_WORKSPACE_ID` (إن لزم) فقط عبر
`tools/push_managed_key.py` أو `npx wrangler secret put`.
