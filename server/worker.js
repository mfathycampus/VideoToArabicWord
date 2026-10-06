/**
 * خادم التفعيل — Cloudflare Worker.
 *
 * وظيفته ثلاث: يحفظ الأكواد، ويربط كل كود بأول جهاز يفعّله، ويوقّع
 * «عقدًا» بوقتٍ **من الخادم** لا من جهاز المستخدم. وبه وحده يصير
 * الإلغاء ممكنًا بعد التوزيع.
 *
 * النشر:
 *   npm i -g wrangler && wrangler login
 *   wrangler kv namespace create LICENSES      # ثم ضع المعرّف في wrangler.toml
 *   wrangler secret put LICENSE_PRIVATE_KEY    # محتوى license_private.key
 *   wrangler secret put ADMIN_TOKEN            # كلمة سرّ لأوامر الإدارة
 *   wrangler deploy
 *
 * المسارات:
 *   POST /activate       {code, device}  → {lease}
 *   POST /recheck        {code, device}  → {lease}
 *   POST /admin/codes    {code, days, ...}            (Bearer ADMIN_TOKEN)
 *   POST /admin/revoke   {code}                       (Bearer ADMIN_TOKEN)
 *   POST /admin/reset-device {code}                   (Bearer ADMIN_TOKEN)
 *   POST /admin/topup    {code, minutes}              (Bearer ADMIN_TOKEN)
 *   POST /admin/config   {units_per_minute, price_in, price_out, usd_sar}
 *   POST /admin/managed-switch {enabled}              (Bearer ADMIN_TOKEN)
 *   POST /v1/messages    (x-api-key = الكود، x-device)  ← الخدمة المُدارة
 *   GET  /managed/balance (x-api-key = الكود، x-device)  ← الرصيد بلا استهلاك
 *   GET  /admin/codes                                  (Bearer ADMIN_TOKEN)
 */

const PREFIX = "VTAW1";

// ── صفحة الإدارة ──────────────────────────────────────────────────────
// صفحة واحدة بلا أي اعتماد خارجي: تُحمَّل من الخادم نفسه وتعمل على
// الجوال. رمز الإدارة يبقى في متصفّحك وحده (localStorage) ويُرسَل
// ترويسةً مع كل نداء — لا يُخزَّن على الخادم ولا يظهر في العنوان.
const ADMIN_PAGE = `<!doctype html>
<html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>أكواد التفعيل — معين</title>
<style>
:root{--bg:#f6f7f6;--card:#fff;--ink:#12211f;--muted:#5d6b68;--line:#dfe5e3;
      --brand:#0b2e2a;--ok:#1c7c54;--warn:#a8630b;--bad:#a52222}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.7 "Segoe UI",system-ui,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 4px}
p.sub{margin:0 0 20px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:16px;margin-bottom:16px;max-width:1000px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
input,select{width:100%;padding:9px 10px;border:1px solid var(--line);
             border-radius:8px;font:inherit;background:#fff;color:inherit}
.row{display:flex;gap:12px;flex-wrap:wrap}
.row>div{flex:1 1 150px}
button{background:var(--brand);color:#fff;border:0;border-radius:8px;
       padding:10px 18px;font:inherit;cursor:pointer}
button.ghost{background:#fff;color:var(--brand);border:1px solid var(--line)}
button.danger{background:#fff;color:var(--bad);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px 6px;border-bottom:1px solid var(--line);text-align:right}
th{color:var(--muted);font-weight:600;font-size:13px}
code{font-family:ui-monospace,Consolas,monospace;font-size:14px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px}
.p-new{background:#eef3f1;color:var(--muted)}
.p-active{background:#e6f4ec;color:var(--ok)}
.p-expired{background:#fdf1e3;color:var(--warn)}
.p-revoked{background:#fbeaea;color:var(--bad)}
.msg{margin-top:10px;font-size:14px}
.out{background:#0b2e2a;color:#e8f3ef;border-radius:8px;padding:12px;
     margin-top:12px;font-family:ui-monospace,Consolas,monospace;
     white-space:pre-wrap;display:none}
</style></head><body>

<h1>أكواد التفعيل</h1>
<p class="sub">معين — لوحة إصدار الأكواد ومتابعتها.</p>

<div class="card" id="auth">
  <label for="token">رمز الإدارة</label>
  <div class="row">
    <div style="flex:3 1 260px"><input id="token" type="password"
      placeholder="ADMIN_TOKEN" autocomplete="current-password"></div>
    <div style="flex:0 0 auto"><button onclick="saveToken()">دخول</button></div>
  </div>
  <div class="msg" id="authMsg"></div>
</div>

<div class="card" id="maker" style="display:none">
  <div class="row">
    <div>
      <label for="plan">الخطة</label>
      <select id="plan" onchange="planChanged()">
        <option value="7">تجربة أسبوع</option>
        <option value="30">شهر</option>
        <option value="90">ثلاثة أشهر</option>
        <option value="365">سنة</option>
        <option value="custom">مدّة مخصّصة…</option>
        <option value="date">حتى تاريخ…</option>
      </select>
    </div>
    <div>
      <label for="tier">الباقة</label>
      <select id="tier" onchange="tierChanged()">
        <option value="trial|1">تجربة</option>
        <option value="solo|2" selected>مفتاحك – فرد</option>
        <option value="team|5">مفتاحك – فريق</option>
        <option value="managed|1">مُدار – فرد (رصيد دقائق)</option>
      </select>
    </div>
    <div>
      <label for="devices">عدد الأجهزة</label>
      <input id="devices" type="number" min="1" max="50" value="2">
    </div>
    <div id="creditBox" style="display:none">
      <label for="credit">الرصيد المبدئي (دقائق)</label>
      <input id="credit" type="number" min="0" value="300">
    </div>
    <div id="daysBox" style="display:none">
      <label for="days">عدد الأيام</label>
      <input id="days" type="number" min="1" value="14">
    </div>
    <div id="dateBox" style="display:none">
      <label for="until">تاريخ الانتهاء</label>
      <input id="until" type="date">
    </div>
    <div>
      <label for="count">عدد الأكواد</label>
      <input id="count" type="number" min="1" max="50" value="1">
    </div>
    <div>
      <label for="note">ملاحظة (اختياري)</label>
      <input id="note" placeholder="اسم المعلّم أو المدرسة">
    </div>
  </div>
  <div style="margin-top:14px">
    <button onclick="createCodes()" id="makeBtn">إصدار</button>
    <button class="ghost" onclick="loadCodes()">تحديث القائمة</button>
  </div>
  <div class="out" id="out"></div>
</div>

<div class="card" id="costCard" style="display:none">
  <div class="msg" id="costSummary" style="margin:0 0 12px"></div>
  <div class="row">
    <div><label for="cfgUpm">نقطة لكل دقيقة</label>
      <input id="cfgUpm" type="number" min="1"></div>
    <div><label for="cfgIn">سعر الدخل ($ لكل مليون رمز)</label>
      <input id="cfgIn" type="number" step="0.01" min="0.01"></div>
    <div><label for="cfgOut">سعر الخرج ($ لكل مليون رمز)</label>
      <input id="cfgOut" type="number" step="0.01" min="0.01"></div>
    <div><label for="cfgFx">الدولار بالريال</label>
      <input id="cfgFx" type="number" step="0.01" min="0.01"></div>
  </div>
  <div style="margin-top:12px">
    <button onclick="saveConfig()">حفظ التسعير</button>
  </div>
  <div class="msg" id="cfgMsg"></div>
</div>

<div class="card" id="listCard" style="display:none">
  <div class="msg" id="managedBar" style="margin:0 0 10px"></div>
  <table id="table"><thead><tr>
    <th>الكود</th><th>الخطة</th><th>المدّة</th><th>الحالة</th>
    <th>الأجهزة</th><th>الرصيد</th><th>الاستهلاك</th><th>فُعِّل في</th><th>ينتهي</th><th>ملاحظة</th><th></th>
  </tr></thead><tbody id="rows"></tbody></table>
  <div class="msg" id="listMsg"></div>
</div>

<script>
const fmt = (t) => t ? new Date(t * 1000).toLocaleDateString("ar-EG",
  {year:"numeric",month:"short",day:"numeric"}) : "—";
const token = () => localStorage.getItem("vtaw_admin") || "";

async function api(path, payload) {
  const options = {
    method: payload ? "POST" : "GET",
    headers: {"authorization": "Bearer " + token(),
              "content-type": "application/json"},
  };
  if (payload) options.body = JSON.stringify(payload);
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || ("خطأ " + response.status));
  return data;
}

function saveToken() {
  localStorage.setItem("vtaw_admin", document.getElementById("token").value.trim());
  loadCodes();
}

function tierChanged() {
  const [kind, devices] = document.getElementById("tier").value.split("|");
  document.getElementById("devices").value = devices;
  document.getElementById("creditBox").style.display = kind === "managed" ? "" : "none";
  if (kind === "managed") {            // الرصيد هو الحدّ، فالكود طويل الصلاحية
    document.getElementById("plan").value = "365";
    planChanged();
  }
}

function tierName() {
  const select = document.getElementById("tier");
  return select.options[select.selectedIndex].text;
}

function planChanged() {
  const plan = document.getElementById("plan").value;
  document.getElementById("daysBox").style.display = plan === "custom" ? "" : "none";
  document.getElementById("dateBox").style.display = plan === "date" ? "" : "none";
}

function chosenDays() {
  const plan = document.getElementById("plan").value;
  if (plan === "custom") return Math.max(1, +document.getElementById("days").value || 1);
  if (plan === "date") {
    const until = document.getElementById("until").value;
    if (!until) throw new Error("اختر تاريخ الانتهاء.");
    const days = Math.ceil((new Date(until + "T23:59:59") - Date.now()) / 86400000);
    if (days < 1) throw new Error("التاريخ يجب أن يكون في المستقبل.");
    return days;
  }
  return +plan;
}

function planLabel(days) {
  if (days === 7) return "تجربة أسبوع";
  if (days === 30) return "شهر";
  if (days === 90) return "ثلاثة أشهر";
  if (days === 365) return "سنة";
  return days + " يومًا";
}

async function createCodes() {
  const button = document.getElementById("makeBtn");
  const out = document.getElementById("out");
  try {
    const days = chosenDays();
    button.disabled = true; button.textContent = "جارٍ الإصدار…";
    const data = await api("/admin/codes", {
      days, max_days: days, count: +document.getElementById("count").value || 1,
      plan: tierName() + " · " + planLabel(days),
      max_devices: Math.max(1, +document.getElementById("devices").value || 1),
      managed: document.getElementById("tier").value.startsWith("managed"),
      credit_minutes: +document.getElementById("credit").value || 0,
      note: document.getElementById("note").value.trim(),
    });
    out.style.display = "block";
    out.textContent = data.codes.join("\\n");
    await loadCodes();
  } catch (err) {
    out.style.display = "block";
    out.textContent = "تعذّر الإصدار: " + err.message;
  } finally {
    button.disabled = false; button.textContent = "إصدار";
  }
}

function statusOf(row) {
  const now = Date.now() / 1000;
  if (row.revoked) return ["ملغى", "p-revoked"];
  if (!row.activated_at) return ["لم يُفعَّل", "p-new"];
  if (now > row.activated_at + row.days * 86400) return ["منتهٍ", "p-expired"];
  return ["فعّال", "p-active"];
}

async function loadCodes() {
  const auth = document.getElementById("auth");
  const message = document.getElementById("authMsg");
  try {
    const data = await api("/admin/codes");
    auth.style.display = "none";
    document.getElementById("maker").style.display = "";
    document.getElementById("listCard").style.display = "";
    const rows = data.codes.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    document.getElementById("rows").innerHTML = rows.map((row) => {
      const [label, css] = statusOf(row);
      const ends = row.activated_at ? fmt(row.activated_at + row.days * 86400) : "—";
      return \`<tr>
        <td><code>\${row.code}</code></td>
        <td>\${row.plan || "—"}</td>
        <td>\${row.days} يومًا</td>
        <td><span class="pill \${css}">\${label}</span></td>
        <td>\${deviceList(row).length}/\${row.max_devices || 1}</td>
        <td>\${row.managed ? ((row.credit_minutes ?? "؟") + " د") : "—"}</td>
        <td>\${row.managed ? ((row.calls || 0) + " نداء · " + (row.cost_sar || 0) + " ر.س") : "—"}</td>
        <td>\${fmt(row.activated_at)}</td>
        <td>\${ends}</td>
        <td>\${row.note || ""}</td>
        <td>
          <button class="ghost" onclick="copyCode('\${row.code}')">نسخ</button>
          \${row.managed ?
            \`<button class="ghost" onclick="topup('\${row.code}')">شحن</button>
             <button class="ghost" onclick="calibrate('\${row.code}', \${row.units_used || 0}, \${row.cost_sar || 0})">معايرة</button>\` : ""}
          \${deviceList(row).length ?
            \`<button class="ghost" onclick="resetDevice('\${row.code}')">تصفير الجهاز</button>\` : ""}
          \${row.revoked ? "" :
            \`<button class="danger" onclick="revoke('\${row.code}')">إلغاء</button>\`}
        </td></tr>\`;
    }).join("");
    document.getElementById("costCard").style.display = data.managed_ready ? "" : "none";
    if (data.config) {
      const totals = data.totals || {};
      const perMinute = totals.minutes_used > 0
        ? (totals.cost_sar / totals.minutes_used).toFixed(3) : null;
      document.getElementById("costSummary").textContent =
        "المستهلك: " + (totals.calls || 0) + " نداء · " + (totals.in_tokens || 0) +
        " رمز دخل · " + (totals.out_tokens || 0) + " رمز خرج ≈ تكلفة " +
        (totals.cost_sar || 0) + " ر.س" +
        (perMinute ? " · التكلفة لكل دقيقة مستهلكة ≈ " + perMinute + " ر.س" : "") +
        " · أرصدة العملاء المتبقية: " + (totals.balance_minutes || 0) + " دقيقة";
      const fields = {cfgUpm: "units_per_minute", cfgIn: "price_in",
                      cfgOut: "price_out", cfgFx: "usd_sar"};
      for (const id in fields) {
        const input = document.getElementById(id);
        if (document.activeElement !== input) input.value = data.config[fields[id]];
      }
    }
    document.getElementById("managedBar").innerHTML = data.managed_ready
      ? ("الخدمة المُدارة: " + (data.managed_enabled ? "تعمل" : "متوقفة") +
         ' <button class="ghost" onclick="switchManaged(' + (!data.managed_enabled) + ')">' +
         (data.managed_enabled ? "إيقاف طارئ" : "تشغيل") + "</button>")
      : "الخدمة المُدارة: غير مهيّأة بعد (ينقصها مفتاح Claude أو المحفظة).";
    document.getElementById("listMsg").textContent =
      rows.length ? "" : "لا أكواد بعد.";
  } catch (err) {
    auth.style.display = "";
    message.textContent = token()
      ? err.message
      : "اكتب رمز الإدارة ثم اضغط دخول.";
  }
}

async function saveConfig() {
  const message = document.getElementById("cfgMsg");
  try {
    await api("/admin/config", {
      units_per_minute: +document.getElementById("cfgUpm").value,
      price_in: +document.getElementById("cfgIn").value,
      price_out: +document.getElementById("cfgOut").value,
      usd_sar: +document.getElementById("cfgFx").value,
    });
    message.textContent = "حُفظ التسعير.";
  } catch (err) { message.textContent = err.message; }
  loadCodes();
}

async function calibrate(code, unitsUsed, cost) {
  if (!unitsUsed) {
    alert("لا استهلاك على هذا الكود بعد. عالج به فيديو معروف المدّة أولًا.");
    return;
  }
  const minutes = +prompt("كم دقيقة فيديو عولجت بهذا الكود؟");
  if (!minutes || minutes <= 0) return;
  const upm = Math.ceil(unitsUsed / minutes);
  if (!confirm("استُهلك " + unitsUsed + " نقطة لـ" + minutes + " دقيقة، أي " + upm +
               " نقطة للدقيقة.\\nتكلفتك الفعلية ≈ " + (cost / minutes).toFixed(3) +
               " ر.س لكل دقيقة فيديو.\\nتطبيق " + upm + " نقطة للدقيقة؟ " +
               "(عايِر قبل البيع: أرصدة العملاء الحالية ستُعرض بالوحدة الجديدة)")) return;
  try { await api("/admin/config", {units_per_minute: upm}); }
  catch (err) { alert(err.message); }
  loadCodes();
}

async function topup(code) {
  const text = prompt("كم دقيقة تُضاف إلى " + code + "؟ (سالب للخصم)", "300");
  if (!text) return;
  try { await api("/admin/topup", {code, minutes: +text}); }
  catch (err) { alert(err.message); }
  loadCodes();
}

async function switchManaged(on) {
  if (!on && !confirm("إيقاف الخدمة المُدارة لكل العملاء فورًا؟")) return;
  try { await api("/admin/managed-switch", {enabled: on}); }
  catch (err) { alert(err.message); }
  loadCodes();
}

const deviceList = (row) => row.devices || (row.device ? [row.device] : []);

async function resetDevice(code) {
  if (!confirm("تصفير أجهزة " + code + "؟ يستطيع العميل تفعيله على جهاز جديد، " +
               "والمدّة لا تبدأ من جديد.")) return;
  try { await api("/admin/reset-device", {code}); }
  catch (err) { alert(err.message); }
  loadCodes();
}

async function copyCode(code) {
  try { await navigator.clipboard.writeText(code); } catch (err) {}
}

async function revoke(code) {
  if (!confirm("إلغاء " + code + "؟ يتوقّف عند أوّل إعادة تحقّق.")) return;
  await api("/admin/revoke", {code});
  loadCodes();
}

if (token()) loadCodes();
</script></body></html>`;


const b64url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const b64decode = (text) =>
  Uint8Array.from(atob(text), (ch) => ch.charCodeAt(0));

async function signingKey(env) {
  // المفتاح الخاص خامٌ (32 بايت) كما يكتبه make_license.py — يُغلَّف
  // بترويسة PKCS#8 لأن Web Crypto لا يقبل الخام لـ Ed25519.
  //
  // ويُقبل أيضًا مفتاحٌ بصيغة PKCS#8 كاملة (48 بايت) أو PEM: لصقٌ خاطئ
  // للسرّ كان يُخرج «Invalid PKCS8 input» بلا دلالة على السبب.
  const text = (env.LICENSE_PRIVATE_KEY || "")
    .replace(/-----[A-Z ]+-----/g, "").replace(/\s+/g, "");
  let raw;
  try {
    raw = b64decode(text.replace(/-/g, "+").replace(/_/g, "/"));
  } catch (err) {
    throw new Error("المفتاح الخاص ليس base64 صالحًا — أعِد ضبط السرّ.");
  }
  if (raw.length === 48) {
    return crypto.subtle.importKey("pkcs8", raw, { name: "Ed25519" },
                                   false, ["sign"]);
  }
  if (raw.length !== 32) {
    throw new Error(
      `طول المفتاح الخاص ${raw.length} بايت والمتوقَّع 32 — ` +
      "السرّ المرفوع ليس محتوى license_private.key.");
  }
  const pkcs8 = new Uint8Array([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70,
    0x04, 0x22, 0x04, 0x20, ...raw,
  ]);
  // اسم الخوارزمية اختلف بين إصدارات Workers: «Ed25519» في الحديث،
  // و«NODE-ED25519» في الأقدم. نجرّب الأول ثم الثاني بدل أن يسقط الطلب
  // بخطأ 1101 لا يقول شيئًا.
  try {
    return await crypto.subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" },
                                         false, ["sign"]);
  } catch (err) {
    return crypto.subtle.importKey(
      "pkcs8", pkcs8,
      { name: "NODE-ED25519", namedCurve: "NODE-ED25519" }, false, ["sign"]);
  }
}

async function makeLease(env, fields) {
  // ترتيب المفاتيح أبجديٌّ إلزامًا: البرنامج يتحقّق من البايتات نفسها.
  const payload = JSON.stringify({
    code: fields.code, device: fields.device, expires_at: fields.expires_at,
    grace_days: fields.grace_days, issued_at: fields.issued_at,
    max_days: fields.max_days, plan: fields.plan, recheck_at: fields.recheck_at,
  }, Object.keys({
    code: 0, device: 0, expires_at: 0, grace_days: 0, issued_at: 0,
    max_days: 0, plan: 0, recheck_at: 0,
  }).sort());
  const bytes = new TextEncoder().encode(payload);
  const key = await signingKey(env);
  const signature = await crypto.subtle.sign(
    { name: key.algorithm?.name || "Ed25519" }, key, bytes);
  return `${PREFIX}.${b64url(bytes)}.${b64url(signature)}`;
}

//: حروف بلا التباس: لا O/0 ولا I/1 — الكود يُملى هاتفيًّا أحيانًا.
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function newCodes(count) {
  const codes = [];
  for (let i = 0; i < count; i += 1) {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const chars = Array.from(bytes, (b) => CODE_ALPHABET[b % 32]).join("");
    codes.push("VTAW-" + chars.match(/.{4}/g).join("-"));
  }
  return codes;
}

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8" },
  });

// المقارنة بعد trim: لصق السرّ من ملف أو طرفية يُلحق به سطرًا جديدًا
// فيفشل التطابق بلا سببٍ ظاهر.
const isAdmin = (request, env) => {
  const secret = (env.ADMIN_TOKEN || "").trim();
  if (!secret) return false;
  const header = (request.headers.get("authorization") || "").trim();
  return header.replace(/^Bearer\s+/i, "").trim() === secret;
};

const deviceListOf = (stored) =>
  Array.isArray(stored.devices) ? [...stored.devices]
    : (stored.device ? [stored.device] : []);

async function activate(request, env, renew) {
  const { code, device } = await request.json();
  if (!code || !device) return json({ error: "طلبٌ ناقص." }, 400);

  const stored = await env.LICENSES.get(code, "json");
  if (!stored) return json({ error: "كود التفعيل غير معروف." }, 404);
  if (stored.revoked) return json({ error: "أُلغي هذا الكود. راجع المزوّد." }, 403);

  // الربط بالأجهزة: الأجهزة الأولى حتى ``max_devices`` تملك الكود، وما
  // بعدها يُرفض. السجلات القديمة تحمل ``device`` واحدًا فتُعامَل كقائمة.
  const devices = deviceListOf(stored);
  const maxDevices = Math.max(1, stored.max_devices || 1);
  const known = devices.includes(device);
  if (!known && devices.length >= maxDevices) {
    return json({
      error: maxDevices === 1
        ? "هذا الكود مُفعَّل على جهاز آخر. اطلب من المزوّد تصفير الجهاز."
        : `بلغ هذا الكود حدّ الأجهزة (${maxDevices}). اطلب من المزوّد تصفير الأجهزة.`,
    }, 403);
  }
  if (!known && renew) {
    // بعد التصفير: الجهاز القديم يُرفض برسالة واضحة، لا «لم يُفعَّل بعد».
    if (devices.length || stored.resets) {
      return json({
        error: "أُعيد ضبط أجهزة هذا الكود. فعِّله من جديد على هذا الجهاز.",
      }, 403);
    }
    return json({ error: "كودٌ لم يُفعَّل بعد." }, 409);
  }

  const now = Math.floor(Date.now() / 1000);
  // المدّة تبدأ من **أول تفعيل** لا من كل تجديد: إعادة التحقّق لا تمدّد.
  const started = stored.activated_at || now;
  const expires = started + (stored.days || 7) * 86400;
  if (now > expires) return json({ error: "انتهت مدّة هذا الكود." }, 403);

  if (!known) {
    devices.push(device);
  }
  stored.devices = devices;
  stored.device = devices[0];          // توافقٌ مع ما يقرؤه سواك
  stored.activated_at = started;
  stored.last_seen = now;
  await env.LICENSES.put(code, JSON.stringify(stored));

  const recheckDays = stored.recheck_days ?? 7;
  return json({
    lease: await makeLease(env, {
      code, device, issued_at: now, expires_at: expires,
      max_days: stored.max_days || stored.days || 7,
      recheck_at: Math.min(expires, now + recheckDays * 86400),
      grace_days: stored.grace_days ?? 3, plan: stored.plan || "",
    }),
  });
}

// ── الخدمة المُدارة ───────────────────────────────────────────────────
// العميل بلا مفتاح Claude: التطبيق يرسل طلبه إلى /v1/messages بكود
// التفعيل بدل المفتاح، والخادم يمرّره بمفتاحنا ويخصم من رصيد الكود.
//
// الرصيد يُخزَّن في Durable Object لكل كود (محفظة): الخصم ذرّي فلا
// يُخصم نداءان متزامنان من الرصيد نفسه، وهو ما لا يضمنه KV.
// الوحدة الداخلية «نقطة» = رموز الدخل + 5×رموز الخرج (نسبة سعر الخرج
// إلى الدخل). و«الدقيقة» وحدة تسويقية = UNITS_PER_MINUTE نقطة، وتُعايَر
// من الاستهلاك الفعلي بعد القياس.

const MANAGED_KEYS = ["system", "messages", "temperature", "top_p", "top_k",
                      "stop_sequences"];
const MAX_BODY_BYTES = 25 * 1024 * 1024;
const OUTPUT_WEIGHT = 5;

const num = (value, fallback) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};
// الإعداد الحي: يُحفظ من صفحة الإدارة في المحفظة العامة ويتقدّم على
// متغيّرات البيئة (القيم الافتراضية). الأسعار بالدولار لكل مليون رمز.
function resolveConfig(env, stored) {
  const saved = stored || {};
  return {
    units_per_minute: num(saved.units_per_minute, num(env.UNITS_PER_MINUTE, 8000)),
    price_in: num(saved.price_in, num(env.PRICE_IN_USD_PER_MTOK, 3)),
    price_out: num(saved.price_out, num(env.PRICE_OUT_USD_PER_MTOK, 15)),
    usd_sar: num(saved.usd_sar, num(env.USD_SAR, 3.75)),
  };
}
const toMinutes = (units, upm) => Math.round((units / upm) * 10) / 10;
const costSar = (inTokens, outTokens, cfg) =>
  Math.round(((inTokens * cfg.price_in + outTokens * cfg.price_out) / 1e6)
             * cfg.usd_sar * 100) / 100;

// بالشكل الذي يفهمه التطبيق (نفس بنية أخطاء Anthropic).
const managedError = (status, type, message, headers = {}) =>
  new Response(JSON.stringify({ type: "error", error: { type, message } }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });

export class Wallet {
  constructor(state) {
    this.state = state;
  }

  async fetch(request) {
    const body = await request.json();
    const store = this.state.storage;
    const now = Date.now();
    let result;

    if (body.op === "get") {
      const values = await store.get(["units", "calls", "in", "out"]);
      result = {
        units: values.get("units") || 0, calls: values.get("calls") || 0,
        in_tokens: values.get("in") || 0, out_tokens: values.get("out") || 0,
      };
    } else if (body.op === "add") {
      const units = Math.round(((await store.get("units")) || 0) + body.units);
      await store.put("units", units);
      result = { units };
    } else if (body.op === "reserve") {
      // يحجز من الرصيد قبل النداء: يحدّ التجاوز حين تتزامن نداءات كثيرة.
      const values = await store.get(["units", "inflight"]);
      let units = values.get("units") || 0;
      const inflight = values.get("inflight") || {};
      for (const [id, started] of Object.entries(inflight)) {
        if (now - started > 600000) delete inflight[id];     // نداءٌ عالق
      }
      if (Object.keys(inflight).length >= (body.max_concurrent || 3)) {
        result = { ok: false, reason: "busy" };
      } else if (units <= 0) {
        result = { ok: false, reason: "empty", units };
      } else {
        const take = Math.min(Math.max(1, Math.ceil(body.want)), units);
        const id = crypto.randomUUID();
        units -= take;
        inflight[id] = now;
        await store.put({ units, inflight });
        result = { ok: true, id, take, units };
      }
    } else if (body.op === "settle") {
      // يردّ الفرق بين المحجوز والمستهلك فعلًا. ``actual`` = 0 عند الفشل.
      const values = await store.get(["units", "inflight", "calls", "in", "out"]);
      const inflight = values.get("inflight") || {};
      delete inflight[body.id];
      const units = Math.round((values.get("units") || 0)
                               + (body.take || 0) - (body.actual || 0));
      await store.put({
        units, inflight,
        calls: (values.get("calls") || 0) + (body.counted ? 1 : 0),
        in: (values.get("in") || 0) + (body.in_tokens || 0),
        out: (values.get("out") || 0) + (body.out_tokens || 0),
      });
      result = { units };
    } else if (body.op === "get_flag") {
      result = {
        enabled: (await store.get("enabled")) !== false,
        config: (await store.get("config")) || {},
      };
    } else if (body.op === "set_config") {
      const next = { ...((await store.get("config")) || {}), ...body.config };
      await store.put("config", next);
      result = { config: next };
    } else if (body.op === "set_flag") {
      await store.put("enabled", Boolean(body.enabled));
      result = { enabled: Boolean(body.enabled) };
    } else {
      result = { error: "عملية غير معروفة." };
    }
    return new Response(JSON.stringify(result),
      { headers: { "content-type": "application/json" } });
  }
}

async function wallet(env, name, payload) {
  const stub = env.WALLET.get(env.WALLET.idFromName(name));
  const response = await stub.fetch("https://wallet/", {
    method: "POST", body: JSON.stringify(payload),
  });
  return response.json();
}

async function globalConfig(env) {
  const state = await wallet(env, "__global__", { op: "get_flag" });
  return { enabled: state.enabled, ...resolveConfig(env, state.config) };
}

function estimateUnits(body, maxTokens) {
  // تقدير متحفّظ للحجز فقط — الخصم النهائي من usage الفعلي.
  let inputTokens = 0;
  const addText = (text) => { inputTokens += Math.ceil(String(text || "").length / 2); };
  if (typeof body.system === "string") addText(body.system);
  else if (Array.isArray(body.system)) body.system.forEach((b) => addText(b && b.text));
  for (const message of body.messages) {
    const content = message && message.content;
    if (typeof content === "string") addText(content);
    else if (Array.isArray(content)) {
      for (const part of content) {
        if (part && part.type === "image") inputTokens += 1600;
        else addText(part && part.text);
      }
    }
  }
  return inputTokens + OUTPUT_WEIGHT * maxTokens;
}

async function managedAuth(request, env) {
  // كود + جهاز + حالة الكود. يعيد ``{ error }`` أو ``{ code, stored }``.
  const fail = (status, type, message) =>
    ({ error: managedError(status, type, message) });
  const code = (request.headers.get("x-api-key") || "").trim().toUpperCase();
  const device = (request.headers.get("x-device") || "").trim();
  if (!code || !device) {
    return fail(401, "authentication_error",
      "كود التفعيل أو معرّف الجهاز ناقص.");
  }
  const stored = await env.LICENSES.get(code, "json");
  if (!stored) {
    return fail(401, "authentication_error", "كود التفعيل غير معروف.");
  }
  if (stored.revoked) {
    return fail(403, "permission_error", "أُلغي هذا الكود. راجع المزوّد.");
  }
  if (!stored.managed) {
    return fail(403, "permission_error",
      "هذا الكود ليس من باقة «مُدار». استخدم مفتاحك الخاص أو اطلب باقة مُدار.");
  }
  if (!stored.activated_at) {
    return fail(403, "permission_error", "فعّل الكود أولًا من نافذة التفعيل.");
  }
  const now = Math.floor(Date.now() / 1000);
  if (now > stored.activated_at + (stored.days || 7) * 86400) {
    return fail(403, "permission_error", "انتهت مدّة هذا الكود.");
  }
  if (!deviceListOf(stored).includes(device)) {
    return fail(403, "permission_error", "هذا الجهاز غير مرتبط بالكود.");
  }
  return { code, stored };
}

// رصيد الكود دون استهلاك: يستعمله التطبيق ليعرضه للمستخدم.
async function managedBalance(request, env) {
  if (!env.WALLET) {
    return managedError(500, "api_error",
      "الخدمة المُدارة غير مهيّأة على الخادم بعد.");
  }
  const auth = await managedAuth(request, env);
  if (auth.error) return auth.error;
  const cfg = await globalConfig(env);
  const info = await wallet(env, auth.code, { op: "get" });
  return json({
    credit_minutes: Math.max(0, toMinutes(info.units, cfg.units_per_minute)),
    enabled: cfg.enabled,
    expires_at: auth.stored.activated_at + (auth.stored.days || 7) * 86400,
  });
}

async function managedMessages(request, env) {
  if (!env.WALLET || !env.ANTHROPIC_API_KEY) {
    return managedError(500, "api_error",
      "الخدمة المُدارة غير مهيّأة على الخادم بعد.");
  }
  const flag = await globalConfig(env);
  if (!flag.enabled) {
    return managedError(503, "overloaded_error",
      "الخدمة المُدارة متوقفة مؤقتًا. حاول لاحقًا.");
  }

  const auth = await managedAuth(request, env);
  if (auth.error) return auth.error;
  const { code } = auth;

  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_BODY_BYTES) {
    return managedError(413, "request_too_large", "الطلب أكبر من الحد المسموح.");
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) {
    return managedError(413, "request_too_large", "الطلب أكبر من الحد المسموح.");
  }
  let input;
  try {
    input = JSON.parse(raw);
  } catch (err) {
    return managedError(400, "invalid_request_error", "طلب غير صالح.");
  }
  if (input.stream) {
    return managedError(400, "invalid_request_error", "البثّ غير مدعوم في الخدمة المُدارة.");
  }
  if (!Array.isArray(input.messages) || !input.messages.length) {
    return managedError(400, "invalid_request_error", "الرسائل ناقصة.");
  }
  // قائمة بيضاء: لا أدوات ولا ميزات أخرى تُستهلك على حسابنا، والنموذج مفروض.
  const outgoing = { model: env.MANAGED_MODEL || "claude-sonnet-5" };
  for (const key of MANAGED_KEYS) {
    if (key in input) outgoing[key] = input[key];
  }
  outgoing.max_tokens = Math.min(
    num(env.MAX_OUTPUT_TOKENS, 16000),
    Math.max(1, Math.floor(Number(input.max_tokens) || 1024)));

  const hold = await wallet(env, code, {
    op: "reserve", want: estimateUnits(outgoing, outgoing.max_tokens),
    max_concurrent: num(env.MAX_CONCURRENT, 3),
  });
  if (!hold.ok) {
    return hold.reason === "busy"
      ? managedError(429, "rate_limit_error",
          "طلبات متزامنة كثيرة لهذا الكود. أعد المحاولة بعد لحظات.",
          { "retry-after": "5" })
      : managedError(402, "billing_error",
          "نفد رصيد الدقائق. اشحن رصيدك ثم أعد المحاولة.");
  }
  const settle = (extra) =>
    wallet(env, code, { op: "settle", id: hold.id, take: hold.take, ...extra });

  const headers = {
    "content-type": "application/json",
    "x-api-key": env.ANTHROPIC_API_KEY.trim(),
    "anthropic-version": request.headers.get("anthropic-version") || "2023-06-01",
  };
  if (env.ANTHROPIC_WORKSPACE_ID) {
    headers["anthropic-workspace-id"] = env.ANTHROPIC_WORKSPACE_ID.trim();
  }

  let upstream, text;
  try {
    upstream = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", headers, body: JSON.stringify(outgoing),
      signal: AbortSignal.timeout(290000),
    });
    text = await upstream.text();
  } catch (err) {
    await settle({ actual: 0 });                  // لم يُستهلك شيء: يُردّ كله
    return managedError(502, "api_error", "تعذّر الوصول إلى Claude. أعد المحاولة.");
  }

  if (!upstream.ok) {
    await settle({ actual: 0 });
    // خطأ في مفتاحنا لا في عميل: لا يُنقل إليه نصّه الذي يوحي له بتغيير مفتاحه.
    const ours = upstream.status === 401 || upstream.status === 403 ||
      (upstream.status === 400 && /workspace/i.test(text));
    if (ours) {
      console.error("managed upstream rejected server key", upstream.status, text.slice(0, 300));
      return managedError(502, "api_error", "عطل في إعداد الخدمة المُدارة. أخبر المزوّد.");
    }
    return new Response(text, { status: upstream.status, headers: {
      "content-type": "application/json; charset=utf-8",
      ...(upstream.headers.get("retry-after")
          ? { "retry-after": upstream.headers.get("retry-after") } : {}),
    } });
  }

  let usage = null;
  try {
    usage = JSON.parse(text).usage || null;
  } catch (err) { /* ردٌّ سليم الحالة بلا usage مفهوم: نخصم المحجوز */ }
  const inTokens = usage
    ? (usage.input_tokens || 0) + (usage.cache_creation_input_tokens || 0)
      + (usage.cache_read_input_tokens || 0) : 0;
  const outTokens = usage ? usage.output_tokens || 0 : 0;
  const after = await settle({
    actual: usage ? inTokens + OUTPUT_WEIGHT * outTokens : hold.take,
    in_tokens: inTokens, out_tokens: outTokens, counted: true,
  });
  return new Response(text, { status: 200, headers: {
    "content-type": "application/json; charset=utf-8",
    "x-maeen-credit-minutes": String(Math.max(0, toMinutes(after.units, flag.units_per_minute))),
  } });
}

async function route(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "POST" && path === "/activate") {
      return activate(request, env, false);
    }
    if (request.method === "POST" && path === "/recheck") {
      return activate(request, env, true);
    }

    if (request.method === "POST" && path === "/v1/messages") {
      return managedMessages(request, env);
    }
    if (request.method === "GET" && path === "/managed/balance") {
      return managedBalance(request, env);
    }

    // صفحة الإدارة: تُخدَم بلا رمز (لا تحوي شيئًا)، والرمز يُطلب داخلها
    // ويُرسَل مع كل نداء. فصلها عن الـAPI يجعل فتحها من الجوال ممكنًا.
    if (request.method === "GET" && path === "/admin") {
      return new Response(ADMIN_PAGE, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    // تشخيصٌ بلا أسرار: يقول إن كان السرّ مضبوطًا أصلًا على الخادم.
    if (request.method === "GET" && path === "/admin/status") {
      return json({
        admin_token_set: Boolean((env.ADMIN_TOKEN || "").trim()),
        signing_key_set: Boolean(env.LICENSE_PRIVATE_KEY),
        kv_bound: Boolean(env.LICENSES),
        wallet_bound: Boolean(env.WALLET),
        anthropic_key_set: Boolean(env.ANTHROPIC_API_KEY),
        managed_model: env.MANAGED_MODEL || "claude-sonnet-5",
        units_per_minute: (env.WALLET ? await globalConfig(env)
                                      : resolveConfig(env)).units_per_minute,
      });
    }

    if (path.startsWith("/admin")) {
      if (!isAdmin(request, env)) {
        return json({
          error: (env.ADMIN_TOKEN || "").trim()
            ? "رمز الإدارة غير صحيح."
            : "لم يُضبط ADMIN_TOKEN على الخادم بعد.",
        }, 401);
      }

      if (request.method === "POST" && path === "/admin/codes") {
        const body = await request.json();
        if (body.managed && !env.WALLET) {
          return json({ error: "المحفظة غير مربوطة بعد — انشر الخادم بالإعداد الجديد." }, 500);
        }
        const count = Math.min(50, Math.max(1, body.count || 1));
        const codes = body.code ? [body.code] : newCodes(count);
        const record = {
          days: body.days || 7, max_days: body.max_days || body.days || 7,
          recheck_days: body.recheck_days ?? 7, grace_days: body.grace_days ?? 3,
          plan: body.plan || "", note: body.note || "",
          max_devices: Math.min(50, Math.max(1, body.max_devices || 1)),
          ...(body.managed ? { managed: true } : {}),
          created_at: Math.floor(Date.now() / 1000),
        };
        for (const code of codes) {
          await env.LICENSES.put(code, JSON.stringify(record));
          const credit = Number(body.credit_minutes) || 0;
          if (body.managed && credit > 0) {
            await wallet(env, code, {
              op: "add",
              units: Math.round(credit * (await globalConfig(env)).units_per_minute),
            });
          }
        }
        return json({ ok: true, codes });
      }

      if (request.method === "POST" && path === "/admin/delete") {
        const { code } = await request.json();
        await env.LICENSES.delete(code);
        return json({ ok: true });
      }

      // التسعير: نقاط الدقيقة وأسعار Claude وسعر الدولار — بلا إعادة نشر.
      if (request.method === "POST" && path === "/admin/config") {
        if (!env.WALLET) return json({ error: "المحفظة غير مربوطة." }, 500);
        const body = await request.json();
        const patch = {};
        for (const key of ["units_per_minute", "price_in", "price_out", "usd_sar"]) {
          if (!(key in body)) continue;
          const value = Number(body[key]);
          if (!Number.isFinite(value) || value <= 0 || value > 1e9) {
            return json({ error: `قيمة غير صالحة: ${key}` }, 400);
          }
          patch[key] = value;
        }
        await wallet(env, "__global__", { op: "set_config", config: patch });
        const { enabled, ...config } = await globalConfig(env);
        return json({ ok: true, config });
      }

      // شحن الرصيد بعد تأكيد التحويل. ``minutes`` سالبة للتصحيح.
      if (request.method === "POST" && path === "/admin/topup") {
        if (!env.WALLET) return json({ error: "المحفظة غير مربوطة." }, 500);
        const { code, minutes } = await request.json();
        const stored = await env.LICENSES.get(code, "json");
        if (!stored) return json({ error: "كود غير معروف." }, 404);
        if (!stored.managed) return json({ error: "هذا الكود ليس من باقة مُدار." }, 400);
        const amount = Number(minutes);
        if (!Number.isFinite(amount) || amount === 0 || Math.abs(amount) > 100000) {
          return json({ error: "عدد دقائق غير صالح." }, 400);
        }
        const upm = (await globalConfig(env)).units_per_minute;
        const result = await wallet(env, code, {
          op: "add", units: Math.round(amount * upm),
        });
        return json({ ok: true, credit_minutes: toMinutes(result.units, upm) });
      }

      // إيقاف طارئ للخدمة المُدارة كلها (أو إعادتها).
      if (request.method === "POST" && path === "/admin/managed-switch") {
        if (!env.WALLET) return json({ error: "المحفظة غير مربوطة." }, 500);
        const { enabled } = await request.json();
        return json({ ok: true, ...(await wallet(env, "__global__", {
          op: "set_flag", enabled: Boolean(enabled) })) });
      }

      // تصفير الأجهزة: يفكّ الربط فقط. ``activated_at`` يبقى، فلا تتجدّد
      // المدّة، ويُحصى عدد المرّات ليظهر من يكرّر الطلب.
      if (request.method === "POST" && path === "/admin/reset-device") {
        const { code } = await request.json();
        const stored = await env.LICENSES.get(code, "json");
        if (!stored) return json({ error: "كود غير معروف." }, 404);
        stored.devices = [];
        delete stored.device;
        stored.resets = (stored.resets || 0) + 1;
        stored.last_reset = Math.floor(Date.now() / 1000);
        await env.LICENSES.put(code, JSON.stringify(stored));
        return json({ ok: true, resets: stored.resets });
      }

      if (request.method === "POST" && path === "/admin/revoke") {
        const { code } = await request.json();
        const stored = await env.LICENSES.get(code, "json");
        if (!stored) return json({ error: "كود غير معروف." }, 404);
        stored.revoked = true;
        await env.LICENSES.put(code, JSON.stringify(stored));
        return json({ ok: true });
      }

      if (request.method === "GET" && path === "/admin/codes") {
        const list = await env.LICENSES.list({ limit: 1000 });
        const rows = [];
        for (const key of list.keys) {
          rows.push({ code: key.name, ...(await env.LICENSES.get(key.name, "json")) });
        }
        let cfg = { enabled: true, ...resolveConfig(env) };
        const totals = { calls: 0, in_tokens: 0, out_tokens: 0, units_used: 0,
                         cost_sar: 0, minutes_used: 0, balance_minutes: 0 };
        if (env.WALLET) {
          cfg = await globalConfig(env);
          await Promise.all(rows.filter((row) => row.managed).map(async (row) => {
            try {
              const info = await wallet(env, row.code, { op: "get" });
              row.credit_minutes = toMinutes(info.units, cfg.units_per_minute);
              row.calls = info.calls;
              row.in_tokens = info.in_tokens;
              row.out_tokens = info.out_tokens;
              row.units_used = info.in_tokens + OUTPUT_WEIGHT * info.out_tokens;
              row.cost_sar = costSar(info.in_tokens, info.out_tokens, cfg);
            } catch (err) {
              row.credit_minutes = null;
            }
          }));
          for (const row of rows) {
            if (!row.managed || row.credit_minutes === null) continue;
            totals.calls += row.calls || 0;
            totals.in_tokens += row.in_tokens || 0;
            totals.out_tokens += row.out_tokens || 0;
            totals.units_used += row.units_used || 0;
            totals.balance_minutes += row.credit_minutes || 0;
          }
          totals.cost_sar = costSar(totals.in_tokens, totals.out_tokens, cfg);
          totals.minutes_used = toMinutes(totals.units_used, cfg.units_per_minute);
          totals.balance_minutes = Math.round(totals.balance_minutes * 10) / 10;
        }
        const { enabled, ...config } = cfg;
        return json({
          codes: rows, managed_enabled: enabled, config, totals,
          managed_ready: Boolean(env.WALLET && env.ANTHROPIC_API_KEY),
          units_per_minute: cfg.units_per_minute,
        });
      }
    }

    return json({ error: "مسار غير معروف." }, 404);
}

export default {
  async fetch(request, env) {
    // بلا هذا يعود الخطأ إلى المستخدم كـ«1101» فقط — رقمٌ لا يشخّص شيئًا.
    try {
      if (!env.LICENSES) {
        return json({ error: "الخادم بلا قاعدة بيانات KV مربوطة." }, 500);
      }
      if (!env.LICENSE_PRIVATE_KEY) {
        return json({ error: "الخادم بلا مفتاح توقيع." }, 500);
      }
      return await route(request, env);
    } catch (err) {
      return json({ error: `عطل في الخادم: ${err && err.message}` }, 500);
    }
  },
};
