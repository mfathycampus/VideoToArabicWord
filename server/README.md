# خادم التفعيل (Cloudflare Worker)

## النشر مرّة واحدة

```bash
npm install -g wrangler
wrangler login
cd server
wrangler kv namespace create LICENSES       # انسخ id إلى wrangler.toml
wrangler secret put LICENSE_PRIVATE_KEY     # محتوى license_private.key
wrangler secret put ADMIN_TOKEN             # كلمة سرّ تخترعها للإدارة
wrangler deploy                             # يطبع عنوان الخادم
```

ثم اربط البرنامج بالعنوان:

```bash
python tools/make_license.py init --server https://vtaw-license.<اسمك>.workers.dev
```

> ‏`init` يولّد مفتاحًا جديدًا ويُبطل الأكواد السابقة. إن كان المفتاح
> موجودًا فحرّر `SERVER_URL` في `licensing/keys.py` يدويًّا بدله.

## صفحة الإدارة

افتح `https://vtaw-license.<اسمك>.workers.dev/admin` من أي جهاز.
تطلب رمز الإدارة مرّة واحدة ويبقى في متصفّحك وحده (لا يُخزَّن على
الخادم ولا يظهر في العنوان). منها:

* إصدار أكواد: خطة جاهزة (أسبوع/شهر/ثلاثة أشهر/سنة)، أو مدّة مخصّصة،
  أو **حتى تاريخ** تختاره فيُحسب عدد الأيام تلقائيًّا.
* عدّة أكواد دفعةً واحدة، مع ملاحظة (اسم المعلّم مثلًا).
* قائمة بكل الأكواد وحالتها: لم يُفعَّل · فعّال · منتهٍ · ملغى، ومتى
  فُعِّل ومتى ينتهي، وزرّا نسخٍ وإلغاء.

والأكواد تُولَّد على الخادم عشوائيًّا — لا تخترعها بيدك.

## إصدار أكواد من الطرفية (بديل)

```bash
python tools/make_license.py new --days 7  --count 10 \
    --server https://... --token <ADMIN_TOKEN> --plan "تجربة أسبوع"
python tools/make_license.py new --days 365 --count 1 --plan "سنة" --server ... --token ...
python tools/make_license.py revoke --code VTAW-XXXX-XXXX-XXXX-XXXX --server ... --token ...
```

## الحدود

الخطة المجانية تكفي آلاف التفعيلات شهريًّا. وإن توقّف الخادم يعمل
البرنامج عند المستخدمين إلى نهاية مهلة إعادة التحقّق (الافتراضي 7 أيام
+ 3 مهلة)، فلا يتعطّل معلّم بسبب انقطاعٍ عندك.
