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
<title>أكواد التفعيل — محوّل الفيديو إلى Word</title>
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
<p class="sub">محوّل الفيديو إلى مستند Word — لوحة إصدار الأكواد ومتابعتها.</p>

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

<div class="card" id="listCard" style="display:none">
  <table id="table"><thead><tr>
    <th>الكود</th><th>الخطة</th><th>المدّة</th><th>الحالة</th>
    <th>فُعِّل في</th><th>ينتهي</th><th>ملاحظة</th><th></th>
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
      plan: planLabel(days), note: document.getElementById("note").value.trim(),
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
        <td>\${fmt(row.activated_at)}</td>
        <td>\${ends}</td>
        <td>\${row.note || ""}</td>
        <td>
          <button class="ghost" onclick="copyCode('\${row.code}')">نسخ</button>
          \${row.revoked ? "" :
            \`<button class="danger" onclick="revoke('\${row.code}')">إلغاء</button>\`}
        </td></tr>\`;
    }).join("");
    document.getElementById("listMsg").textContent =
      rows.length ? "" : "لا أكواد بعد.";
  } catch (err) {
    auth.style.display = "";
    message.textContent = token()
      ? err.message
      : "اكتب رمز الإدارة ثم اضغط دخول.";
  }
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

async function activate(request, env, renew) {
  const { code, device } = await request.json();
  if (!code || !device) return json({ error: "طلبٌ ناقص." }, 400);

  const stored = await env.LICENSES.get(code, "json");
  if (!stored) return json({ error: "كود التفعيل غير معروف." }, 404);
  if (stored.revoked) return json({ error: "أُلغي هذا الكود. راجع المزوّد." }, 403);

  // الربط بالجهاز: أول تفعيل يملك الكود، وما بعده يُرفض.
  if (stored.device && stored.device !== device) {
    return json({ error: "هذا الكود مُفعَّل على جهاز آخر." }, 403);
  }
  if (!stored.device && renew) {
    return json({ error: "كودٌ لم يُفعَّل بعد." }, 409);
  }

  const now = Math.floor(Date.now() / 1000);
  // المدّة تبدأ من **أول تفعيل** لا من كل تجديد: إعادة التحقّق لا تمدّد.
  const started = stored.activated_at || now;
  const expires = started + (stored.days || 7) * 86400;
  if (now > expires) return json({ error: "انتهت مدّة هذا الكود." }, 403);

  if (!stored.device) {
    stored.device = device;
    stored.activated_at = started;
  }
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

async function route(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "POST" && path === "/activate") {
      return activate(request, env, false);
    }
    if (request.method === "POST" && path === "/recheck") {
      return activate(request, env, true);
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
        const count = Math.min(50, Math.max(1, body.count || 1));
        const codes = body.code ? [body.code] : newCodes(count);
        const record = {
          days: body.days || 7, max_days: body.max_days || body.days || 7,
          recheck_days: body.recheck_days ?? 7, grace_days: body.grace_days ?? 3,
          plan: body.plan || "", note: body.note || "",
          created_at: Math.floor(Date.now() / 1000),
        };
        for (const code of codes) {
          await env.LICENSES.put(code, JSON.stringify(record));
        }
        return json({ ok: true, codes });
      }

      if (request.method === "POST" && path === "/admin/delete") {
        const { code } = await request.json();
        await env.LICENSES.delete(code);
        return json({ ok: true });
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
        return json({ codes: rows });
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
