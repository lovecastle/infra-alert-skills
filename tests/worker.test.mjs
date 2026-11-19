// Tests for skills/monitor-cloudflare/assets/worker/src/index.js:  node --test tests/
// D1 is stood in for by node:sqlite (a real SQL engine, so the Worker's SQL is really run); fetch is replaced so
// probes and the chat services answer what each test says.
import assert from "node:assert/strict";
import nodeCrypto from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, test } from "node:test";
import { compose, dur, evaluate, handle, heartbeats, report, send, sign, tick } from "../skills/monitor-cloudflare/assets/worker/src/index.js";

crypto.subtle.timingSafeEqual ??= (a, b) => nodeCrypto.timingSafeEqual(a, b); // workerd has it built in

function fakeD1() {
  const db = new DatabaseSync(":memory:");
  const exec = (sql, args) => (/^\s*select/i.test(sql) ? { success: true, results: db.prepare(sql).all(...args) } : (db.prepare(sql).run(...args), { success: true, results: [] }));
  const stmt = (sql, args = []) => ({ sql, args, bind: (...a) => stmt(sql, a) });
  return { prepare: (sql) => stmt(sql), batch: async (list) => list.map((s) => exec(s.sql, s.args)) };
}

let env, answers, sent;
const cfg = (more = {}) => ({ name: "prod", timezone: "UTC", dailyReportHour: null, failThreshold: 2, okThreshold: 2, remindHours: 4, probes: [], heartbeats: [], ...more });
beforeEach(() => {
  env = { DB: fakeD1(), TG_BOT_TOKEN: "T", TG_CHAT_ID: "-100", HEARTBEAT_TOKEN: "hbsecret" };
  answers = {}; // url → status | { status, body } | Error
  sent = []; // Telegram messages
  globalThis.fetch = async (url, init = {}) => {
    if (String(url).includes("/sendMessage")) {
      const body = JSON.parse(init.body);
      sent.push(body);
      return new Response('{"ok":true}', { status: answers.telegram ?? 200 });
    }
    const a = answers[url] ?? 200;
    if (a instanceof Error) throw a;
    return new Response(a.body ?? "", { status: a.status ?? a });
  };
});
const texts = () => sent.map((m) => m.text);

test("dur", () => assert.deepEqual([45, 720, 14520, 183600].map(dur), ["45s", "12m", "4h 2m", "2d 3h"]));

test("evaluate: opens after failThreshold, resolves after okThreshold, reminds while critical", () => {
  const st = {}, bad = [{ key: "x", level: "crit", text: "x down" }], good = [{ key: "x", level: "ok", text: "x up" }];
  assert.deepEqual(evaluate(st, bad, 0), []);
  assert.deepEqual(evaluate(st, bad, 60), [{ kind: "open", level: "crit", key: "x", text: "x down" }]);
  assert.deepEqual(evaluate(st, bad, 120), []);
  assert.deepEqual(evaluate(st, bad, 60 + 4 * 3600), [{ kind: "remind", level: "crit", key: "x", text: "x down (for 4h 0m)" }]);
  assert.deepEqual(evaluate(st, good, 60 + 4 * 3600 + 60), []);
  assert.deepEqual(evaluate(st, good, 60 + 4 * 3600 + 120), [{ kind: "resolved", level: "ok", key: "x", text: "x up (after 4h 2m)" }]);
  assert.deepEqual(evaluate(st, [], 0), []);
  assert.deepEqual(st.checks, {}); // a check no longer read is forgotten
});

test("compose and report", () => {
  assert.deepEqual(compose("prod", [{ kind: "open", level: "crit", key: "a", text: "site: HTTP 502" }, { kind: "resolved", level: "ok", key: "b", text: "api: HTTP 200 (after 3m)" }]),
    { title: "[infra-alerts] 🔴 URGENT | prod: 1 problem, 1 resolved", lines: ["🔴 site: HTTP 502", "✅ api: HTTP 200 (after 3m)", "#urgent #prod"], loud: true, cat: "urgent" });
  const quiet = compose("my site", [{ kind: "resolved", level: "ok", key: "b", text: "x" }], { resolved: "ĐÃ ỔN" });
  assert.deepEqual([quiet.loud, quiet.cat, quiet.title, quiet.lines.at(-1)], [false, "resolved", "[infra-alerts] ✅ ĐÃ ỔN | my site: 1 resolved", "#resolved #my_site"]);
  assert.deepEqual(report("prod", [{ level: "ok", text: "a" }, { level: "crit", text: "b" }]), { title: "[infra-alerts] 📊 STATS | prod: daily report, 1 of 2 checks failing", lines: ["🔴 b", "✅ a", "#stats #prod"], loud: false, cat: "stats" });
});

test("heartbeats: never seen is not down; silent past grace is", () => {
  const c = cfg({ heartbeats: [{ name: "vps-01", graceMinutes: 5 }, { name: "rtx", graceMinutes: 5 }, { name: "new" }] });
  const got = heartbeats(c, { "hb:vps-01": JSON.stringify({ at: 1000, open: 2 }), "hb:rtx": JSON.stringify({ at: 100 }) }, 1060);
  assert.deepEqual(got.map((r) => [r.level, r.text]), [["ok", "vps-01: heartbeat 1m ago, 2 alerts open on it"], ["crit", "rtx: no heartbeat for 16m"], ["ok", "new: waiting for its first heartbeat"]]);
});

test("sign matches values computed with node:crypto", async () => {
  const hmac = (k, m) => nodeCrypto.createHmac("sha256", k).update(m).digest("base64");
  assert.equal(await sign("1599360473\nsecret", ""), hmac("1599360473\nsecret", ""));
  assert.equal(await sign("secret", "1599360473000\nsecret"), "h/3f4uEADBeKkVuXX2NS1pXSEqARbYb1RV0kmXPBfn0="); // the same vector as the Python test
});

test("tick: a probe down two minutes is told once, then its recovery with the duration", async () => {
  const c = cfg({ probes: [{ name: "site", url: "https://site/" }, { name: "api", url: "https://api/health", contains: "ok" }] });
  answers["https://api/health"] = { status: 200, body: "status: ok" };
  await tick(env, 0, c);
  assert.deepEqual(texts(), []); // all fine on a first run: nothing to say
  answers["https://site/"] = 502;
  await tick(env, 60, c);
  assert.deepEqual(texts(), []);
  await tick(env, 120, c);
  assert.deepEqual(texts(), ["<b>[infra-alerts] 🔴 URGENT | prod: 1 problem</b>\n🔴 site: HTTP 502\n#urgent #prod"]);
  assert.deepEqual([sent[0].chat_id, sent[0].parse_mode, sent[0].disable_notification], ["-100", "HTML", false]);
  await tick(env, 180, c);
  assert.equal(sent.length, 1);
  answers["https://site/"] = 200;
  await tick(env, 240, c);
  await tick(env, 300, c);
  assert.match(texts()[1], /^<b>\[infra-alerts\] ✅ RESOLVED \| prod: 1 resolved<\/b>\n✅ site: HTTP 200 in \d+ ms \(after 3m\)\n#resolved #prod$/);
  assert.equal(sent[1].disable_notification, true);
});

test("tick: wrong body, timeout and thrown errors are down", async () => {
  const c = cfg({ failThreshold: 1, probes: [{ name: "api", url: "https://api/", contains: "ok" }, { name: "slow", url: "https://slow/" }, { name: "dns", url: "https://dns/" }] });
  answers["https://api/"] = { status: 200, body: "maintenance" };
  answers["https://slow/"] = Object.assign(new Error("x"), { name: "TimeoutError" });
  answers["https://dns/"] = new Error("getaddrinfo ENOTFOUND dns");
  await tick(env, 0, c);
  assert.equal(texts()[0], '<b>[infra-alerts] 🔴 URGENT | prod: 3 problems</b>\n🔴 api: HTTP 200 but the answer lacks "ok"\n🔴 slow: no answer (timed out)\n🔴 dns: no answer (getaddrinfo ENOTFOUND dns)\n#urgent #prod');
});

test("tick: a message Telegram refused goes out with the next run", async () => {
  const c = cfg({ failThreshold: 1, probes: [{ name: "site", url: "https://site/" }] });
  answers["https://site/"] = 500;
  answers.telegram = 500;
  await tick(env, 0, c);
  answers.telegram = 200;
  sent.length = 0;
  await tick(env, 60, c);
  assert.deepEqual(texts(), ["<b>[infra-alerts] 🔴 URGENT | prod: 1 problem</b>\n🔴 site: HTTP 500\n#urgent #prod"]);
  await tick(env, 120, c);
  assert.equal(sent.length, 1);
});

test("telegram: markup refused with 400 goes again as plain text", async () => {
  const c = cfg({ failThreshold: 1, probes: [{ name: "a<b", url: "https://site/" }] });
  answers["https://site/"] = 500;
  let first = true;
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => (String(url).includes("/sendMessage") && first ? ((first = false), sent.push(JSON.parse(init.body)), new Response("{}", { status: 400 })) : real(url, init));
  await tick(env, 0, c);
  assert.deepEqual(sent.map((m) => [m.text, m.parse_mode]), [["<b>[infra-alerts] 🔴 URGENT | prod: 1 problem</b>\n🔴 a&lt;b: HTTP 500\n#urgent #prod", "HTML"], ["[infra-alerts] 🔴 URGENT | prod: 1 problem\n🔴 a<b: HTTP 500\n#urgent #prod", undefined]]);
});

test("heartbeat endpoint: token needed, known hosts only; silence past grace alerts and a ping recovers", async () => {
  const c = cfg({ heartbeats: [{ name: "vps-01", graceMinutes: 5 }] });
  const ping = (path, token, now) => handle(new Request(`https://w.example${path}`, { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body: JSON.stringify({ host: "vps-01", open: 0, crit: 0 }) }), env, c, now);
  assert.equal((await ping("/hb/vps-01", null, 0)).status, 401);
  assert.equal((await ping("/hb/vps-01", "wrong-one", 0)).status, 401);
  assert.equal((await ping("/hb/other", "hbsecret", 0)).status, 404);
  assert.equal((await ping("/hb/vps-01", "hbsecret", 0)).status, 200);
  await tick(env, 60, c);
  await tick(env, 300, c);
  assert.deepEqual(texts(), []);
  await tick(env, 361, c);
  assert.deepEqual(texts(), ["<b>[infra-alerts] 🔴 URGENT | prod: 1 problem</b>\n🔴 vps-01: no heartbeat for 6m\n#urgent #prod"]);
  await ping("/hb/vps-01", "hbsecret", 900);
  await tick(env, 901, c);
  assert.equal(texts()[1], "<b>[infra-alerts] ✅ RESOLVED | prod: 1 resolved</b>\n✅ vps-01: heartbeat 1s ago (after 9m)\n#resolved #prod");
  env.HEARTBEAT_TOKEN = "";
  assert.equal((await ping("/hb/vps-01", "", 0)).status, 401); // no token set: nothing is accepted
});

test("health: stale until the cron has run, and again once it stops", async () => {
  const get = (now) => handle(new Request("https://w.example/health"), env, cfg(), now);
  assert.equal((await get(0)).status, 503);
  await tick(env, 1000, cfg());
  assert.equal((await get(1100)).status, 200);
  assert.equal((await get(1400)).status, 503);
  assert.equal((await handle(new Request("https://w.example/"), env, cfg())).status, 404);
});

test("test endpoint sends through the channels", async () => {
  const res = await handle(new Request("https://w.example/test", { method: "POST", headers: { Authorization: "Bearer hbsecret" } }), env, cfg());
  assert.equal(await res.text(), "sent");
  assert.match(texts()[0], /test message/);
  env.TG_BOT_TOKEN = "";
  assert.equal((await handle(new Request("https://w.example/test", { method: "POST", headers: { Authorization: "Bearer hbsecret" } }), env, cfg())).status, 502);
});

test("daily report: once a day at the local hour", async () => {
  const c = cfg({ timezone: "Asia/Ho_Chi_Minh", dailyReportHour: 8, probes: [{ name: "site", url: "https://site/" }] });
  const at = (iso) => Math.floor(Date.parse(iso) / 1000);
  await tick(env, at("2026-11-18T00:59:00Z"), c); // 07:59 in Vietnam
  assert.deepEqual(texts(), []);
  await tick(env, at("2026-11-18T01:00:00Z"), c); // 08:00
  await tick(env, at("2026-11-18T01:01:00Z"), c);
  assert.equal(sent.length, 1);
  assert.match(texts()[0], /^<b>\[infra-alerts\] 📊 STATS \| prod: daily report, all 1 checks OK<\/b>\n✅ site: HTTP 200 in \d+ ms\n#stats #prod$/);
  await tick(env, at("2026-11-19T01:00:00Z"), c);
  assert.equal(sent.length, 2);
});

test("other channels: WeCom, Feishu (signed), DingTalk (signed), webhook", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => (calls.push([String(url), JSON.parse(init.body)]), new Response('{"errcode":0,"code":0}'));
  env = { DB: fakeD1(), QYWX_KEY: "K", FSKEY: "F", FSSECRET: "S", DD_BOT_TOKEN: "D", DD_BOT_SECRET: "S2", WEBHOOK_URL: "https://hook.example/x", HEARTBEAT_TOKEN: "t" };
  const res = await handle(new Request("https://w.example/test", { method: "POST", headers: { Authorization: "Bearer t" } }), env, cfg());
  assert.equal(res.status, 200);
  const by = Object.fromEntries(calls.map(([u, b]) => [new URL(u).host, [u, b]]));
  assert.equal(by["qyapi.weixin.qq.com"][0], "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K");
  assert.match(by["qyapi.weixin.qq.com"][1].text.content, /test message/);
  const fs = by["open.feishu.cn"][1];
  assert.equal(fs.sign, nodeCrypto.createHmac("sha256", `${fs.timestamp}\nS`).update("").digest("base64"));
  assert.match(by["oapi.dingtalk.com"][0], /^https:\/\/oapi\.dingtalk\.com\/robot\/send\?access_token=D&timestamp=\d{13}&sign=[\w%]+$/);
  assert.equal(by["hook.example"][1].source, "infra-alerts");
});

test("a category with its own Telegram topic goes there", async () => {
  Object.assign(env, { TG_THREAD_ID: "7", TG_THREAD_URGENT: "9" });
  for (const cat of ["urgent", "stats", undefined]) await send(env, { title: "t", lines: [], loud: false, cat });
  assert.deepEqual(sent.map((m) => m.message_thread_id), [9, 7, 7]);
});

test("/incidents: its own token; what the Worker sees plus what live hosts said in their last heartbeat", async () => {
  const c = cfg({ probes: [{ name: "site", url: "https://site.example/" }], heartbeats: [{ name: "vps-01", graceMinutes: 5 }, { name: "vps-02", graceMinutes: 5 }] });
  const get = (token, now) => handle(new Request("https://w.example/incidents", { headers: token ? { Authorization: `Bearer ${token}` } : {} }), env, c, now);
  const beat = (host, now, alerts) => handle(new Request(`https://w.example/hb/${host}`, { method: "POST", headers: { Authorization: "Bearer hbsecret" }, body: JSON.stringify({ open: alerts.length, alerts }) }), env, c, now);
  assert.equal((await get("hbsecret", 0)).status, 401); // no AGENT_TOKEN set: closed, and a host's token never opens it
  env.AGENT_TOKEN = "agentsecret";
  assert.equal((await get("hbsecret", 0)).status, 401);
  assert.deepEqual(await (await get("agentsecret", 0)).json(), { now: 0, incidents: [] });
  answers["https://site.example/"] = 502;
  await beat("vps-01", 0, [{ key: "disk:/", level: "crit", text: "disk /: 96% used", since: -600 }, { key: "x".repeat(300), level: "nonsense", text: 5 }]);
  await beat("vps-02", 0, [{ key: "memory", level: "warn", text: "memory: 91% used", since: -60 }]);
  await tick(env, 0, c);
  await tick(env, 60, c);
  await beat("vps-01", 400, [{ key: "disk:/", level: "crit", text: "disk /: 97% used", since: -600 }]);
  await tick(env, 420, c); // vps-02 has now been silent past its grace: one incident, not its stale alerts
  const got = (await (await get("agentsecret", 420)).json()).incidents;
  assert.deepEqual(got.map((i) => [i.id, i.host, i.level, i.text, i.source]), [
    ["prod/probe:site", "prod", "crit", "site: HTTP 502", "worker"],
    ["prod/host:vps-02", "vps-02", "crit", "vps-02: no heartbeat for 7m", "worker"],
    ["vps-01/disk:/", "vps-01", "crit", "disk /: 97% used", "host"],
  ]);
  assert.equal(got[2].since, -600);
});
