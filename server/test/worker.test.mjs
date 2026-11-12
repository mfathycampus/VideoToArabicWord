// اختبارات خادم التفعيل — تشغّل ``worker.js`` نفسه على KV ومحفظة وهميّتين.
//
//     cd server && node --test test/*.test.mjs
//
// لا تبعيات: ``node:test`` وحده (Node 22+). ما لا يُختبر هنا: سلوك منصّة
// Cloudflare نفسها (اتساق KV النهائي، إلغاء الطلب عند انقطاع العميل) —
// يُحاكى أثره فقط.
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import worker, { Wallet } from "../worker.js";

// ── بدائل المنصّة ────────────────────────────────────────────────────
class FakeKV {
  constructor() {
    this.map = new Map();
    this.puts = [];
    this.pageSize = 1000;
    this.stale = new Map();          // مفتاح ← قيمة قديمة تعيدها القراءة
    this.failGet = null;
  }
  async get(key, type) {
    if (this.failGet) throw this.failGet;
    const value = this.stale.has(key) ? this.stale.get(key) : this.map.get(key);
    if (value === undefined) return null;
    return type === "json" ? JSON.parse(value) : value;
  }
  async put(key, value) { this.puts.push(key); this.map.set(key, String(value)); }
  async delete(key) { this.map.delete(key); }
  async list({ limit = 1000, cursor, prefix } = {}) {
    const all = [...this.map.keys()].filter((k) => !prefix || k.startsWith(prefix)).sort();
    const start = cursor ? Number(cursor) : 0;
    const end = start + Math.min(limit, this.pageSize);
    return {
      keys: all.slice(start, end).map((name) => ({ name })),
      list_complete: end >= all.length,
      cursor: end >= all.length ? undefined : String(end),
    };
  }
  record(code) { return JSON.parse(this.map.get(code)); }
}

class FakeStorage {                  // ينسخ القيم كما يفعل تخزين Durable Objects
  constructor() { this.map = new Map(); }
  async get(keys) {
    if (Array.isArray(keys)) {
      const out = new Map();
      for (const key of keys) {
        if (this.map.has(key)) out.set(key, structuredClone(this.map.get(key)));
      }
      return out;
    }
    return this.map.has(keys) ? structuredClone(this.map.get(keys)) : undefined;
  }
  async put(key, value) {
    if (typeof key === "object") {
      for (const [k, v] of Object.entries(key)) this.map.set(k, structuredClone(v));
    } else {
      this.map.set(key, structuredClone(value));
    }
  }
  async delete(key) { this.map.delete(key); }
}

// الكائن الدائم لا يسلّم حدثًا جديدًا وعمليةُ تخزينٍ جارية («بوّابة الدخل»)، فمعالجٌ
// لا ينتظر إلا التخزين يُنفَّذ ذرّيًّا. البديل يحاكي ذلك بطابور لكل كائن — وبدونه
// تتداخل الطلبات المتزامنة هنا بما لا يحدث على المنصّة.
class FakeWallets {
  constructor() { this.objects = new Map(); this.queues = new Map(); }
  idFromName(name) { return name; }
  get(id) {
    if (!this.objects.has(id)) this.objects.set(id, new Wallet({ storage: new FakeStorage() }));
    const object = this.objects.get(id);
    return {
      fetch: (url, init) => {
        const run = () => object.fetch(new Request(url, init));
        const next = (this.queues.get(id) || Promise.resolve()).then(run, run);
        this.queues.set(id, next.then(() => {}, () => {}));
        return next;
      },
    };
  }
}

const ADMIN = "admin-token-for-tests";
const DEVICE = "0123456789abcdef0123";
const OTHER = "fedcba98765432100000";

function makeEnv(extra = {}) {
  return {
    LICENSES: new FakeKV(),
    WALLET: new FakeWallets(),
    LICENSE_PRIVATE_KEY: Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64"),
    ADMIN_TOKEN: ADMIN,
    ANTHROPIC_API_KEY: "sk-test",
    UNITS_PER_MINUTE: "8000",
    ...extra,
  };
}

async function call(env, method, path, { body, headers = {}, admin = false, ctx } = {}) {
  const init = { method, headers: { ...headers } };
  if (admin) init.headers.authorization = "Bearer " + ADMIN;
  if (body !== undefined) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
    init.headers["content-type"] = "application/json";
  }
  const response = await worker.fetch(new Request("https://server.test" + path, init), env, ctx);
  const text = await response.text();
  let data = null;
  try { data = JSON.parse(text); } catch (err) { /* صفحة HTML */ }
  return { status: response.status, headers: response.headers, text, data };
}

const walletOp = async (env, name, payload) =>
  (await env.WALLET.get(name).fetch("https://wallet/", {
    method: "POST", body: JSON.stringify(payload) })).json();

async function newCode(env, options = {}) {
  const made = await call(env, "POST", "/admin/codes",
    { admin: true, body: { days: 30, max_devices: 1, ...options } });
  assert.equal(made.status, 200, made.text);
  return made.data.codes[0];
}

async function managedCode(env, minutes = 10) {
  const code = await newCode(env, { managed: true, tier: "managed", credit_minutes: minutes });
  const activated = await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  assert.equal(activated.status, 200, activated.text);
  return code;
}

const messages = (env, code, extra = {}) => call(env, "POST", "/v1/messages", {
  headers: { "x-api-key": code, "x-device": DEVICE },
  body: { max_tokens: 1000, messages: [{ role: "user", content: "مرحبا" }] },
  ...extra,
});

const realFetch = globalThis.fetch;
const realNow = Date.now;
let now;
beforeEach(() => { now = 1_800_000_000_000; Date.now = () => now; });
afterEach(() => { globalThis.fetch = realFetch; Date.now = realNow; });

const upstreamOk = (usage = { input_tokens: 1000, output_tokens: 200 }) =>
  new Response(JSON.stringify({ content: [{ type: "text", text: "تم" }], usage }), { status: 200 });

// ══ ① المحفظة: الحجز العالق يُردّ، ومرّة واحدة ═══════════════════════
test("حجزٌ لم يُسوَّ يعود إلى الرصيد بعد أن يُعدّ عالقًا", async () => {
  const env = makeEnv();
  await walletOp(env, "C", { op: "add", units: 10000 });
  const hold = await walletOp(env, "C", { op: "reserve", want: 4000 });
  assert.equal(hold.ok, true);
  assert.equal((await walletOp(env, "C", { op: "get" })).units, 6000);

  now += 9 * 60 * 1000;                               // ما زال ضمن المهلة
  assert.equal((await walletOp(env, "C", { op: "get" })).units, 6000);

  now += 2 * 60 * 1000;                               // 11 دقيقة: النداء مات
  assert.equal((await walletOp(env, "C", { op: "get" })).units, 10000);
});

test("الحجز العالق لا يحجب نداءات الكود التالية ولا يترك الرصيد «نافدًا»", async () => {
  const env = makeEnv();
  await walletOp(env, "C", { op: "add", units: 3000 });
  await walletOp(env, "C", { op: "reserve", want: 3000 });       // أخذ الرصيد كله ومات
  assert.equal((await walletOp(env, "C", { op: "reserve", want: 100 })).reason, "empty");
  now += 11 * 60 * 1000;
  const next = await walletOp(env, "C", { op: "reserve", want: 100 });
  assert.equal(next.ok, true);
  assert.equal(next.units, 2900);
});

test("تسويةٌ تصل بعد ردّ الحجز العالق تخصم الفعلي ولا تردّ المحجوز مرّتين", async () => {
  const env = makeEnv();
  await walletOp(env, "C", { op: "add", units: 10000 });
  const hold = await walletOp(env, "C", { op: "reserve", want: 4000 });
  now += 11 * 60 * 1000;
  assert.equal((await walletOp(env, "C", { op: "get" })).units, 10000);
  const settled = await walletOp(env, "C", {
    op: "settle", id: hold.id, take: hold.take, actual: 1500, counted: true });
  assert.equal(settled.units, 8500);
});

test("التسوية العادية: يُردّ المحجوز ويُخصم الفعلي، وتكرارها لا يردّ ثانيةً", async () => {
  const env = makeEnv();
  await walletOp(env, "C", { op: "add", units: 10000 });
  const hold = await walletOp(env, "C", { op: "reserve", want: 4000 });
  const first = await walletOp(env, "C", { op: "settle", id: hold.id, take: 4000, actual: 1000 });
  assert.equal(first.units, 9000);
  const again = await walletOp(env, "C", { op: "settle", id: hold.id, take: 4000, actual: 0 });
  assert.equal(again.units, 9000);
});

test("المحجوز يُؤخذ من سجلّ المحفظة لا من قول المنادي", async () => {
  const env = makeEnv();
  await walletOp(env, "C", { op: "add", units: 10000 });
  const hold = await walletOp(env, "C", { op: "reserve", want: 4000 });
  const settled = await walletOp(env, "C", { op: "settle", id: hold.id, take: 999999, actual: 0 });
  assert.equal(settled.units, 10000);
});

test("حجوزات بالصيغة القديمة (رقم) تُسوّى كما كانت وتُنظَّف بلا ردّ مجهول", async () => {
  const env = makeEnv();
  await walletOp(env, "C", { op: "add", units: 6000 });
  const storage = env.WALLET.objects.get("C").state.storage;
  await storage.put("inflight", { old1: now, old2: now });
  const settled = await walletOp(env, "C", { op: "settle", id: "old1", take: 4000, actual: 1000 });
  assert.equal(settled.units, 9000);
  now += 11 * 60 * 1000;
  assert.equal((await walletOp(env, "C", { op: "get" })).units, 9000);
  assert.deepEqual(await storage.get("inflight"), {});
});

// ══ ① النداء المُدار: خصم الفعلي، وميزانية زمنية ═════════════════════
test("نداءٌ ناجح يخصم الاستهلاك الفعلي ويعيد الرصيد في الترويسة", async () => {
  const env = makeEnv();
  const code = await managedCode(env, 10);                       // 80000 نقطة
  globalThis.fetch = async () => upstreamOk();                   // 1000 + 5×200 = 2000
  const reply = await messages(env, code);
  assert.equal(reply.status, 200, reply.text);
  assert.equal((await walletOp(env, code, { op: "get" })).units, 78000);
  assert.equal(reply.headers.get("x-maeen-credit-minutes"), "9.8");
});

test("فشل الوصول إلى Claude لا يخصم شيئًا", async () => {
  const env = makeEnv();
  const code = await managedCode(env, 10);
  globalThis.fetch = async () => { throw new Error("network down"); };
  const reply = await messages(env, code);
  assert.equal(reply.status, 502);
  assert.equal((await walletOp(env, code, { op: "get" })).units, 80000);
});

test("لا إعادة محاولة حين لا يبقى من الميزانية ما يكفي — يعود الخطأ العابر فورًا", async () => {
  const env = makeEnv({ MANAGED_BUDGET_MS: "100" });
  const code = await managedCode(env, 10);
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response('{"type":"error"}', { status: 529, headers: { "retry-after": "1" } });
  };
  const reply = await messages(env, code);
  assert.equal(calls, 1);
  assert.equal(reply.status, 529);
  assert.equal((await walletOp(env, code, { op: "get" })).units, 80000);
});

test("الميزانية لا تُرفع من الإعداد فوق ما يفترضه التطبيق", async () => {
  const env = makeEnv({ MANAGED_BUDGET_MS: "99999999" });
  const code = await managedCode(env, 10);
  let timeoutMs = null;
  const realTimeout = AbortSignal.timeout;
  AbortSignal.timeout = (ms) => { timeoutMs = ms; return realTimeout.call(AbortSignal, ms); };
  try {
    globalThis.fetch = async () => upstreamOk();
    await messages(env, code);
  } finally {
    AbortSignal.timeout = realTimeout;
  }
  assert.ok(timeoutMs <= 280000, String(timeoutMs));
});

test("النداء المُدار يُسلَّم إلى waitUntil ليُسوّى إن انقطع العميل", async () => {
  const env = makeEnv();
  const code = await managedCode(env, 10);
  globalThis.fetch = async () => upstreamOk();
  const waited = [];
  await messages(env, code, { ctx: { waitUntil: (promise) => waited.push(promise) } });
  assert.ok(waited.length >= 1);
  await Promise.all(waited);
});

// ══ ② سجلّ الترخيص لا يُكتب من التطبيق إلا عند ربط جهاز جديد ═════════
test("الإحصاءات تُحفظ في مفتاحها ولا تمسّ سجلّ الترخيص", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  const before = env.LICENSES.map.get(code);
  env.LICENSES.puts.length = 0;

  const reply = await call(env, "POST", "/stats", {
    body: { code, device: DEVICE, app_version: "1.22.4", stats: { jobs_total: 3 } } });
  assert.equal(reply.status, 200, reply.text);
  assert.deepEqual(env.LICENSES.puts, [`usage:${code}:${DEVICE.slice(0, 12)}`]);
  assert.equal(env.LICENSES.map.get(code), before);
});

test("إحصاءاتٌ أو إعادة تحقّق بنسخةٍ قديمة من السجلّ لا تمحو الإلغاء", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  const stale = env.LICENSES.map.get(code);                  // قبل الإلغاء
  await call(env, "POST", "/admin/revoke", { admin: true, body: { code } });
  assert.equal(env.LICENSES.record(code).revoked, true);

  env.LICENSES.stale.set(code, stale);                       // KV يعيد القديم لثوانٍ
  await call(env, "POST", "/stats", { body: { code, device: DEVICE, stats: { jobs_total: 1 } } });
  await call(env, "POST", "/recheck", { body: { code, device: DEVICE } });
  env.LICENSES.stale.clear();

  assert.equal(env.LICENSES.record(code).revoked, true);
});

test("إعادة تحقّقٍ لجهاز معروف لا تكتب شيئًا، وجهازٌ جديد يُكتب مرّة", async () => {
  const env = makeEnv();
  const code = await newCode(env, { max_devices: 2 });
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  env.LICENSES.puts.length = 0;
  const recheck = await call(env, "POST", "/recheck", { body: { code, device: DEVICE } });
  assert.equal(recheck.status, 200, recheck.text);
  assert.match(recheck.data.lease, /^VTAW1\./);
  assert.deepEqual(env.LICENSES.puts, []);

  await call(env, "POST", "/activate", { body: { code, device: OTHER } });
  assert.deepEqual(env.LICENSES.puts, [code]);
  assert.deepEqual(env.LICENSES.record(code).devices, [DEVICE, OTHER]);
});

test("حدّ الأجهزة والإلغاء ما زالا يُرفضان", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  assert.equal((await call(env, "POST", "/activate", { body: { code, device: OTHER } })).status, 403);
  await call(env, "POST", "/admin/revoke", { admin: true, body: { code } });
  assert.equal((await call(env, "POST", "/recheck", { body: { code, device: DEVICE } })).status, 403);
});

test("الكود يُقبل بأحرف صغيرة ومسافات حوله", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  const reply = await call(env, "POST", "/activate",
    { body: { code: `  ${code.toLowerCase()} `, device: DEVICE } });
  assert.equal(reply.status, 200, reply.text);
});

// ══ ③ المدخلات ولوحة الإدارة ═════════════════════════════════════════
test("شكل الكود والجهاز يُفحص قبل أي قراءة من KV", async () => {
  const env = makeEnv();
  env.LICENSES.failGet = new Error("KV must not be reached");
  for (const body of [
    { code: "usage:VTAW-AAAA:0123", device: DEVICE },
    { code: "VTAW-<b>", device: DEVICE },
    { code: "VTAW-AAAA-BBBB", device: { $ne: 1 } },
    { code: "VTAW-AAAA-BBBB", device: "x".repeat(200) },
    { code: ["VTAW-AAAA"], device: DEVICE },
    "not json",
  ]) {
    for (const path of ["/activate", "/recheck", "/stats"]) {
      const reply = await call(env, "POST", path, { body });
      assert.equal(reply.status, 400, `${path} ${JSON.stringify(body)} → ${reply.text}`);
    }
  }
});

test("app_version من العميل: شكل رقم إصدار أو لا شيء", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  const key = `usage:${code}:${DEVICE.slice(0, 12)}`;
  for (const [sent, kept] of [["<iframe src=//x.io>", ""], ["1.22.4", "1.22.4"],
                              ["1.22.4-beta+7", "1.22.4-beta+7"], ['"><b>', ""]]) {
    await call(env, "POST", "/stats",
      { body: { code, device: DEVICE, app_version: sent, stats: { jobs_total: 1 } } });
    assert.equal(env.LICENSES.record(key).app_version, kept, sent);
  }
});

test("صفحة الإدارة تهرّب كل قيمة ولا تركّب الكود داخل onclick", async () => {
  const env = makeEnv();
  const page = await call(env, "GET", "/admin");
  assert.equal(page.status, 200);
  assert.match(page.text, /const esc = /);
  for (const unsafe of ["'${row.code}'", '<td>${row.note || ""}</td>',
                        '${u.app_version || "—"}', "<td>${row.plan || \"—\"}</td>",
                        "<code>${row.code}</code>"]) {
    assert.ok(!page.text.includes(unsafe), unsafe);
  }
  assert.ok(!/onclick="(copyCode|topup|calibrate|resetDevice|revoke)\(/.test(page.text));
  assert.match(page.text, /data-act="revoke" data-code="\$\{code\}"/);
});

test("دالة التهريب في الصفحة تعطّل الوسوم وعلامات الاقتباس", async () => {
  const page = (await call(makeEnv(), "GET", "/admin")).text;
  const source = page.match(/const ESC = [^\n]+\nconst esc = [^\n]+/)[0];
  const esc = new Function(source + "; return esc;")();
  assert.equal(esc(`<img src=x onerror="a('b')">&`),
    "&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;");
  assert.equal(esc(null), "");
  assert.equal(esc(0), "0");
});

test("صفحات HTML تحمل ترويسات تمنع التضمين وتحميل أي شيء خارجي", async () => {
  const env = makeEnv({ CONTACT_INFO: '<script src="//x.io"></script>' });
  for (const path of ["/admin", "/privacy", "/terms"]) {
    const page = await call(env, "GET", path);
    const csp = page.headers.get("content-security-policy");
    assert.match(csp, /default-src 'none'/, path);
    assert.match(csp, /frame-ancestors 'none'/, path);
    assert.equal(page.headers.get("x-frame-options"), "DENY", path);
    assert.ok(!page.text.includes('<script src="//x.io">'), path);
  }
});

test("أوامر الإدارة ترفض الرمز الخاطئ وتقبل الصحيح", async () => {
  const env = makeEnv();
  for (const token of ["", "wrong", ADMIN + "x", ADMIN.slice(0, -1)]) {
    const reply = await call(env, "GET", "/admin/codes",
      { headers: { authorization: "Bearer " + token } });
    assert.equal(reply.status, 401, token);
  }
  assert.equal((await call(env, "GET", "/admin/codes", { admin: true })).status, 200);
});

test("/admin/status بلا رمز لا يكشف النموذج ولا التسعير", async () => {
  const env = makeEnv();
  const open = (await call(env, "GET", "/admin/status")).data;
  assert.equal(open.anthropic_key_set, true);                 // تحتاجه أدوات النشر
  for (const hidden of ["managed_model", "units_per_minute", "alerts_configured"]) {
    assert.ok(!(hidden in open), hidden);
  }
  const full = (await call(env, "GET", "/admin/status", { admin: true })).data;
  assert.equal(full.units_per_minute, 8000);
  assert.ok(full.managed_model);
});

test("عطلٌ داخلي لا يُعيد نصّ الاستثناء إلا للمشرف", async () => {
  const env = makeEnv();
  env.LICENSES.failGet = new Error("internal-detail-xyz");
  const body = { code: "VTAW-AAAA-BBBB-CCCC-DDDD", device: DEVICE };
  const client = await call(env, "POST", "/activate", { body });
  assert.equal(client.status, 500);
  assert.ok(!client.text.includes("internal-detail-xyz"));
  assert.match(client.data.error, /مرجع [0-9a-f]{8}/);
  const admin = await call(env, "POST", "/admin/revoke", { admin: true, body });
  assert.ok(admin.text.includes("internal-detail-xyz"));
});

test("كودٌ مخصّص: شكله يُفحص ولا يُكتب فوق كودٍ قائم", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  const again = await call(env, "POST", "/admin/codes", { admin: true, body: { code, days: 365 } });
  assert.equal(again.status, 409);
  assert.deepEqual(env.LICENSES.record(code).devices, [DEVICE]);
  const bad = await call(env, "POST", "/admin/codes",
    { admin: true, body: { code: "<img src=x>", days: 7 } });
  assert.equal(bad.status, 400);
});

test("حقول الكود تُضبط أنواعها وحدودها عند الإصدار", async () => {
  const env = makeEnv();
  const code = await newCode(env, {
    days: "<b>", max_devices: 9999, note: "ن".repeat(500), plan: 42, recheck_days: -3 });
  const record = env.LICENSES.record(code);
  assert.equal(record.days, 7);
  assert.equal(record.max_days, 7);
  assert.equal(record.max_devices, 50);
  assert.equal(record.note.length, 120);
  assert.equal(record.plan, "42");
  assert.equal(record.recheck_days, 0);
});

// ══ قائمة الأكواد: كل الصفحات + إحصاءات المفاتيح المستقلة ═════════════
test("قائمة الإدارة تجمع كل صفحات KV وتضمّ إحصاءات كل جهاز إلى كوده", async () => {
  const env = makeEnv();
  env.LICENSES.pageSize = 7;
  const codes = [];
  for (let i = 0; i < 20; i += 1) codes.push(await newCode(env));
  await call(env, "POST", "/activate", { body: { code: codes[3], device: DEVICE } });
  await call(env, "POST", "/stats",
    { body: { code: codes[3], device: DEVICE, app_version: "1.22.4",
              stats: { jobs_total: 5, completed: 4 } } });
  // سجلٌّ من قبل 1.22.4 يحمل إحصاءاته بداخله
  const legacy = env.LICENSES.record(codes[5]);
  legacy.usage = { aaaaaaaaaaaa: { jobs_total: 2, completed: 2, reported_at: 1 } };
  env.LICENSES.map.set(codes[5], JSON.stringify(legacy));

  const list = await call(env, "GET", "/admin/codes", { admin: true });
  assert.equal(list.status, 200, list.text);
  assert.equal(list.data.codes.length, 20);
  assert.ok(list.data.codes.every((row) => !row.code.startsWith("usage:")));
  const row = list.data.codes.find((item) => item.code === codes[3]);
  assert.equal(row.usage[DEVICE.slice(0, 12)].jobs_total, 5);
  assert.equal(list.data.codes.find((item) => item.code === codes[5]).usage.aaaaaaaaaaaa.jobs_total, 2);
  assert.equal(list.data.usage_totals.users, 2);
  assert.equal(list.data.usage_totals.jobs_total, 7);
});

test("حذف كودٍ يحذف إحصاءاته معه", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await call(env, "POST", "/activate", { body: { code, device: DEVICE } });
  await call(env, "POST", "/stats", { body: { code, device: DEVICE, stats: { jobs_total: 1 } } });
  await call(env, "POST", "/admin/delete", { admin: true, body: { code } });
  assert.deepEqual([...env.LICENSES.map.keys()], []);
});

// ══ صفحة الإدارة تعمل فعلًا: سكربتها يُشغَّل على DOM وهمي ════════════
// ``node --check`` لا يرى داخل القالب النصّي: علامة اقتباس مائلة في تعليقٍ
// داخل السكربت أنهت القالب مرّة وأسقطت الخادم كله عند التحميل.
function runAdminPage(pageText, payload) {
  const script = pageText.match(/<script>([\s\S]*)<\/script>/)[1];
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      elements.set(id, {
        id, innerHTML: "", textContent: "", value: "", style: {}, listeners: {},
        options: [], selectedIndex: 0, disabled: false,
        addEventListener(type, handler) { this.listeners[type] = handler; },
      });
    }
    return elements.get(id);
  };
  const copied = [];
  const scope = {
    document: { getElementById: element, activeElement: null },
    localStorage: { getItem: () => "tok", setItem() {} },
    fetch: async () => ({ ok: true, json: async () => payload }),
    navigator: { clipboard: { writeText: async (text) => { copied.push(text); } } },
    alert() {}, confirm: () => false, prompt: () => null,
  };
  const api = new Function(...Object.keys(scope),
    script + "; return { loadCodes };")(...Object.values(scope));
  return { api, element, copied };
}

test("سكربت صفحة الإدارة يُحلَّل ويعرض القيم العدائية نصًّا لا HTML", async () => {
  const page = (await call(makeEnv(), "GET", "/admin")).text;
  const hostile = '<img src=x onerror="alert(1)">';
  const { api, element, copied } = runAdminPage(page, {
    codes: [{
      code: "VTAW-AAAA-BBBB-CCCC-DDDD", note: hostile, plan: "<q>خطة</q>", days: 30,
      max_devices: "<i>2</i>", managed: true, credit_minutes: 5, calls: 1, cost_sar: 2,
      units_used: 100, devices: ["d"], activated_at: 1_700_000_000,
      usage: { d: { jobs_total: 1, completed: 1, app_version: "<u>v</u>",
                    last_job_day: "<s>day</s>", reported_at: 1_700_000_000 } },
    }, { code: "VTAW-EEEE-FFFF-GGGG-HHHH", days: 7,
         usage: { d: { jobs_total: 1, app_version: hostile } } }],
    usage_totals: { users: "<em>2</em>" }, managed_ready: false,
  });
  await api.loadCodes();

  const html = element("rows").innerHTML + element("usageRows").innerHTML
    + element("usageTotals").innerHTML;
  for (const tag of ["<img", "<q>", "<i>", "<u>", "<s>", "<em>"]) {
    assert.ok(!html.includes(tag), tag);
  }
  assert.ok(html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
  assert.ok(!/onclick=/.test(html));

  // الأزرار تعمل من مستمعٍ واحد يقرأ data-code
  const button = { dataset: { act: "copy", code: "VTAW-AAAA-BBBB-CCCC-DDDD" } };
  element("rows").listeners.click({ target: { closest: () => button } });
  assert.deepEqual(copied, ["VTAW-AAAA-BBBB-CCCC-DDDD"]);
});

// ══ 1.22.6: حالة الترخيص في كائن الكود — ذرّية، و KV مرآة ═════════════
const activate = (env, code, device, path = "/activate") =>
  call(env, "POST", path, { body: { code, device } });
const licState = (env, code) => walletOp(env, code, { op: "lic_state" });

test("جهازان يفعّلان كودًا بجهاز واحد في اللحظة نفسها: واحد فقط ينجح", async () => {
  const env = makeEnv();
  const code = await newCode(env, { max_devices: 1 });
  const replies = await Promise.all([activate(env, code, DEVICE), activate(env, code, OTHER)]);
  assert.deepEqual(replies.map((r) => r.status).sort(), [200, 403]);
  assert.equal((await licState(env, code)).devices.length, 1);
  assert.equal(env.LICENSES.record(code).devices.length, 1);
});

test("عشرة أجهزة تتسابق على كود بثلاثة: ثلاثة فقط", async () => {
  const env = makeEnv();
  const code = await newCode(env, { max_devices: 3 });
  const devices = Array.from({ length: 10 }, (_, i) => `device-number-${i}`);
  const replies = await Promise.all(devices.map((d) => activate(env, code, d)));
  assert.equal(replies.filter((r) => r.status === 200).length, 3);
  assert.equal((await licState(env, code)).devices.length, 3);
});

test("جهازٌ جديد يُربط بنسخةٍ من KV تسبق الإلغاء: يُرفض ولا يُمحى الإلغاء", async () => {
  const env = makeEnv();
  const code = await newCode(env, { max_devices: 2 });
  await activate(env, code, DEVICE);
  const stale = env.LICENSES.map.get(code);                  // غير ملغى، وفيه مكان لجهاز ثانٍ
  await call(env, "POST", "/admin/revoke", { admin: true, body: { code } });

  env.LICENSES.stale.set(code, stale);
  const reply = await activate(env, code, OTHER);
  env.LICENSES.stale.clear();

  assert.equal(reply.status, 403);
  assert.match(reply.data.error, /أُلغي/);
  assert.equal(env.LICENSES.record(code).revoked, true);
  assert.deepEqual((await licState(env, code)).devices, [DEVICE]);
});

test("كودٌ أُلغي للتوّ لا يواصل استهلاك «المُدار» ولو تأخرت مرآة KV", async () => {
  const env = makeEnv();
  const code = await managedCode(env, 10);
  const stale = env.LICENSES.map.get(code);
  await call(env, "POST", "/admin/revoke", { admin: true, body: { code } });
  env.LICENSES.stale.set(code, stale);
  let upstreamCalls = 0;
  globalThis.fetch = async () => { upstreamCalls += 1; return upstreamOk(); };
  const reply = await messages(env, code);
  assert.equal(reply.status, 403);
  assert.equal(upstreamCalls, 0);
  assert.equal((await walletOp(env, code, { op: "get" })).units, 80000);
});

test("التصفير يفكّ الأجهزة ولا يجدّد المدّة، والجهاز القديم يُرفض برسالته", async () => {
  const env = makeEnv();
  const code = await newCode(env, { days: 30 });
  await activate(env, code, DEVICE);
  const started = (await licState(env, code)).activated_at;
  now += 10 * 86400 * 1000;
  const reset = await call(env, "POST", "/admin/reset-device", { admin: true, body: { code } });
  assert.equal(reset.data.resets, 1);

  const old = await activate(env, code, DEVICE, "/recheck");
  assert.equal(old.status, 403);
  assert.match(old.data.error, /أُعيد ضبط/);

  assert.equal((await activate(env, code, OTHER)).status, 200);
  const state = await licState(env, code);
  assert.deepEqual(state.devices, [OTHER]);
  assert.equal(state.activated_at, started);                 // المدّة من أول تفعيل
  const record = env.LICENSES.record(code);
  assert.deepEqual(record.devices, [OTHER]);
  assert.equal(record.device, OTHER);
  assert.equal(record.resets, 1);
});

test("المدّة تنتهي من أول تفعيل ولو أُعيد التحقّق أو صُفّر الجهاز", async () => {
  const env = makeEnv();
  const code = await newCode(env, { days: 7 });
  await activate(env, code, DEVICE);
  now += 8 * 86400 * 1000;
  assert.equal((await activate(env, code, DEVICE, "/recheck")).status, 403);
  await call(env, "POST", "/admin/reset-device", { admin: true, body: { code } });
  const again = await activate(env, code, OTHER);
  assert.equal(again.status, 403);
  assert.match(again.data.error, /انتهت/);
});

test("كودٌ من قبل الترقية: حالته في KV تُعتمد عند أول وصول ولا يُطلب تفعيل جديد", async () => {
  const env = makeEnv();
  const started = Math.floor(now / 1000) - 5 * 86400;
  env.LICENSES.map.set("VTAW-OLD1-OLD1-OLD1-OLD1", JSON.stringify({
    days: 30, max_days: 30, max_devices: 1, plan: "شهر",
    device: DEVICE, activated_at: started }));                // صيغة قديمة: device مفرد
  env.LICENSES.puts.length = 0;

  const recheck = await activate(env, "VTAW-OLD1-OLD1-OLD1-OLD1", DEVICE, "/recheck");
  assert.equal(recheck.status, 200, recheck.text);
  assert.deepEqual(env.LICENSES.puts, []);                    // المرآة مطابقة: لا كتابة
  const state = await licState(env, "VTAW-OLD1-OLD1-OLD1-OLD1");
  assert.deepEqual(state.devices, [DEVICE]);
  assert.equal(state.activated_at, started);
  assert.equal((await activate(env, "VTAW-OLD1-OLD1-OLD1-OLD1", OTHER)).status, 403);
});

test("كودٌ ملغى من قبل الترقية يبقى ملغى", async () => {
  const env = makeEnv();
  env.LICENSES.map.set("VTAW-DEAD-DEAD-DEAD-DEAD", JSON.stringify({
    days: 30, max_devices: 1, devices: [DEVICE], activated_at: 1, revoked: true }));
  const reply = await activate(env, "VTAW-DEAD-DEAD-DEAD-DEAD", DEVICE, "/recheck");
  assert.equal(reply.status, 403);
  assert.equal((await licState(env, "VTAW-DEAD-DEAD-DEAD-DEAD")).revoked, true);
});

test("الإلغاء لا يُمحى: حالةٌ في الكائن غير ملغاة و KV يقول ملغى ⇒ ملغى", async () => {
  const env = makeEnv();
  const code = await newCode(env);
  await activate(env, code, DEVICE);
  const record = env.LICENSES.record(code);
  env.LICENSES.map.set(code, JSON.stringify({ ...record, revoked: true }));   // إلغاء بنسخة أقدم من الخادم
  assert.equal((await activate(env, code, DEVICE, "/recheck")).status, 403);
  assert.equal((await licState(env, code)).revoked, true);
});

test("مرآة KV التي فاتتها كتابة تُصلَح عند أول إعادة تحقّق", async () => {
  const env = makeEnv();
  const code = await newCode(env, { max_devices: 2 });
  const before = env.LICENSES.map.get(code);
  await activate(env, code, DEVICE);
  env.LICENSES.map.set(code, before);                         // كأن الكتابة لم تكتمل
  const recheck = await activate(env, code, DEVICE, "/recheck");
  assert.equal(recheck.status, 200);
  assert.deepEqual(env.LICENSES.record(code).devices, [DEVICE]);
});

test("حذف كودٍ يمسح حالته: الاسم نفسه يُصدر من جديد بلا أجهزة", async () => {
  const env = makeEnv();
  const code = await newCode(env, { code: "VTAW-CUST-CUST-CUST-CUST" });
  await activate(env, code, DEVICE);
  await call(env, "POST", "/admin/delete", { admin: true, body: { code } });
  await newCode(env, { code });
  assert.equal((await activate(env, code, OTHER)).status, 200);
  assert.deepEqual((await licState(env, code)).devices, [OTHER]);
});

test("بلا محفظة مربوطة: التفعيل والإلغاء يعملان على KV كما كانا", async () => {
  const env = makeEnv({ WALLET: undefined });
  const code = await newCode(env);
  assert.equal((await activate(env, code, DEVICE)).status, 200);
  assert.equal((await activate(env, code, OTHER)).status, 403);
  assert.equal((await activate(env, code, DEVICE, "/recheck")).status, 200);
  await call(env, "POST", "/admin/reset-device", { admin: true, body: { code } });
  assert.equal((await activate(env, code, OTHER)).status, 200);
  await call(env, "POST", "/admin/revoke", { admin: true, body: { code } });
  assert.equal((await activate(env, code, OTHER, "/recheck")).status, 403);
  assert.equal(env.LICENSES.record(code).revoked, true);
});

test("عقد التفعيل يحمل نهاية المدّة من حالة الكائن", async () => {
  const env = makeEnv();
  const code = await newCode(env, { days: 30 });
  const first = await activate(env, code, DEVICE);
  now += 3 * 86400 * 1000;
  const second = await activate(env, code, DEVICE, "/recheck");
  const payload = (lease) => JSON.parse(Buffer.from(lease.split(".")[1], "base64url").toString());
  assert.equal(payload(first.data.lease).expires_at, payload(second.data.lease).expires_at);
  assert.equal(payload(second.data.lease).expires_at,
    (await licState(env, code)).activated_at + 30 * 86400);
});
