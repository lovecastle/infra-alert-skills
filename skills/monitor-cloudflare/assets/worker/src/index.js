// infra-alerts Worker: the watcher that lives outside your servers.
//
// Every minute (Cron Trigger) it asks each URL in config.js from Cloudflare's network and looks at when each host last
// sent a heartbeat (POST /hb/<name>, what infrawatch.py does after every run). A probe failing failThreshold minutes in
// a row, or a host silent past its grace, is told to the chat once; so is its recovery, with how long it lasted. A
// machine that is off cannot say so itself: this is what notices.
//
// State is one small table in D1 (made on first use). GET /health answers 200 while the cron is running, 503 once it
// has been silent 5 minutes: point a host's [http] check at it and the hosts watch the watcher back. GET /incidents
// (its own token) lists everything open right now, here and on the hosts: what an agent reads to know what to work on.
import config from "./config.js";

const OK = "ok", CRIT = "crit";
const RANK = { ok: 0, warn: 1, crit: 2 }, ICON = { ok: "✅", warn: "🟠", crit: "🔴" };
const PREFIX = "[infra-alerts]";
// What a message asks of whoever reads it, as in infrawatch.py: a word in the title (config.js labels, any language)
// and a tag on the last line that is the same everywhere. On Telegram a category can have its own topic
// (secret TG_THREAD_URGENT, TG_THREAD_RESOLVED, TG_THREAD_STATS).
const CATS = { urgent: "🔴", attention: "🟠", resolved: "✅", stats: "📊" };
const LABELS = { urgent: "URGENT", attention: "ATTENTION", resolved: "RESOLVED", stats: "STATS" };
const headline = (cat, name, said, labels) => `${PREFIX} ${CATS[cat]} ${{ ...LABELS, ...labels }[cat]} | ${name}: ${said}`;
const tags = (cat, name) => `#${cat} #${name.replace(/\W/g, "_")}`;

export function dur(s) {
  s = Math.floor(s);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}

// Readings [{ key, level, text, instant }] → notices [{ kind, level, key, text }], kind open / worse / remind /
// resolved. The same state machine as infrawatch.py's evaluate: only changes are told, a critical alert still open is
// told again every remindHours. st.checks is updated in place.
export function evaluate(st, readings, now, { failThreshold = 2, okThreshold = 2, remindHours = 4 } = {}) {
  const checks = (st.checks ||= {}), notices = [];
  for (const r of readings) {
    const c = (checks[r.key] ||= { level: OK, bad: 0, good: 0 });
    const needBad = r.instant ? 1 : failThreshold, needGood = r.instant ? 1 : okThreshold;
    if (r.level !== OK) {
      c.good = 0;
      if (c.level === OK) {
        if (++c.bad >= needBad) { Object.assign(c, { level: r.level, since: now, told: now }); notices.push({ kind: "open", level: r.level, key: r.key, text: r.text }); }
      } else if (RANK[r.level] > RANK[c.level]) {
        Object.assign(c, { level: r.level, told: now });
        notices.push({ kind: "worse", level: r.level, key: r.key, text: r.text });
      } else {
        c.level = r.level;
        if (r.level === CRIT && remindHours && now - c.told >= remindHours * 3600) { c.told = now; notices.push({ kind: "remind", level: r.level, key: r.key, text: `${r.text} (for ${dur(now - c.since)})` }); }
      }
      c.text = r.text;
    } else if (c.level !== OK) {
      if (++c.good >= needGood) {
        notices.push({ kind: "resolved", level: OK, key: r.key, text: `${r.text} (after ${dur(now - c.since)})` });
        checks[r.key] = { level: OK, bad: 0, good: 0 };
      }
    } else c.bad = 0;
  }
  const seen = new Set(readings.map((r) => r.key));
  for (const k of Object.keys(checks)) if (!seen.has(k)) delete checks[k]; // taken out of config.js
  return notices;
}

export function compose(name, notices, labels) {
  const n = (kind) => notices.filter((x) => x.kind === kind).length, fresh = n("open") + n("worse");
  const parts = [fresh && `${fresh} problem${fresh > 1 ? "s" : ""}`, n("remind") && `${n("remind")} still open`, n("resolved") && `${n("resolved")} resolved`].filter(Boolean);
  const bad = notices.filter((x) => x.kind !== "resolved").map((x) => x.level);
  const cat = bad.includes(CRIT) ? "urgent" : bad.length ? "attention" : "resolved";
  return { title: headline(cat, name, parts.join(", "), labels), lines: [...notices.map((x) => `${ICON[x.level]} ${x.kind === "remind" ? "still: " : ""}${x.text}`), tags(cat, name)], loud: fresh > 0, cat };
}

// The daily message: everything watched, bad first. It arriving at all says the Worker is alive.
export function report(name, readings, labels) {
  const bad = readings.filter((r) => r.level !== OK).length;
  const lines = [...readings].sort((a, b) => RANK[b.level] - RANK[a.level]).map((r) => `${ICON[r.level]} ${r.text}`);
  return { title: headline("stats", name, `daily report, ${bad ? `${bad} of ${readings.length} checks failing` : `all ${readings.length} checks OK`}`, labels), lines: [...lines, tags("stats", name)], loud: false, cat: "stats" };
}

// ---------- readings ----------
async function probe(p) {
  const key = `probe:${p.name}`, started = Date.now();
  try {
    const res = await fetch(p.url, { method: p.method || "GET", headers: { "User-Agent": "infra-alerts-probe", ...p.headers }, signal: AbortSignal.timeout(p.timeoutMs || 10_000) });
    const fine = p.status ? res.status === p.status : res.status >= 200 && res.status < 400; // redirects are followed
    if (fine && p.contains && !(await res.text()).includes(p.contains)) return { key, level: CRIT, text: `${p.name}: HTTP ${res.status} but the answer lacks "${p.contains}"` };
    await res.body?.cancel().catch(() => {});
    return { key, level: fine ? OK : CRIT, text: `${p.name}: HTTP ${res.status}${fine ? ` in ${Date.now() - started} ms` : ""}` };
  } catch (e) {
    return { key, level: CRIT, text: `${p.name}: no answer (${e.name === "TimeoutError" ? "timed out" : String(e.message || e).slice(0, 80)})` };
  }
}

// A host is down once it has been silent past its grace; one that has never pinged is still being set up.
export function heartbeats(cfg, rows, now) {
  return (cfg.heartbeats || []).map((h) => {
    const key = `host:${h.name}`, beat = rows[`hb:${h.name}`] && JSON.parse(rows[`hb:${h.name}`]);
    if (!beat) return { key, level: OK, instant: true, text: `${h.name}: waiting for its first heartbeat` };
    const silent = now - beat.at, late = silent > (h.graceMinutes ?? 5) * 60;
    return { key, level: late ? CRIT : OK, instant: true, text: late ? `${h.name}: no heartbeat for ${dur(silent)}`
      : `${h.name}: heartbeat ${dur(silent)} ago${beat.open ? `, ${beat.open} alert${beat.open > 1 ? "s" : ""} open on it` : ""}` };
  });
}

// Everything open right now: what this Worker sees (probes, silent hosts) and what each host said in its last
// heartbeat. A host that has gone silent is one incident; what it last said about itself is no longer news.
export function incidents(cfg, rows, now) {
  const out = Object.entries(JSON.parse(rows.state || "{}").checks || {}).filter(([, c]) => c.level !== OK)
    .map(([key, c]) => ({ id: `${cfg.name}/${key}`, host: key.startsWith("host:") ? key.slice(5) : cfg.name, key, level: c.level, text: c.text, since: c.since, source: "worker" }));
  for (const h of cfg.heartbeats || []) {
    const beat = rows[`hb:${h.name}`] && JSON.parse(rows[`hb:${h.name}`]);
    if (beat && now - beat.at <= (h.graceMinutes ?? 5) * 60) for (const a of beat.alerts || []) out.push({ id: `${h.name}/${a.key}`, host: h.name, ...a, source: "host" });
  }
  return out;
}

// ---------- channels: each resolves true when the service took the message ----------
const post = (url, body) => fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(10_000) });
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
const plain = (m, limit = 1900) => new TextDecoder("utf-8", { ignoreBOM: true }).decode(new TextEncoder().encode([m.title, ...m.lines].join("\n")).slice(0, limit)).replace(/�+$/, ""); // WeCom's text limit is 2048 bytes
const coded = async (res, field) => res.ok && (await res.json().catch(() => ({})))[field] === 0;

export async function sign(key, msg) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg)))));
}

const CHANNELS = {
  async telegram(env, m) {
    const url = `${(env.TG_API_HOST || "https://api.telegram.org").replace(/\/+$/, "")}/bot${env.TG_BOT_TOKEN}/sendMessage`;
    const base = { chat_id: env.TG_CHAT_ID, disable_web_page_preview: true, disable_notification: !m.loud };
    const thread = env[`TG_THREAD_${(m.cat || "").toUpperCase()}`] || env.TG_THREAD_ID; // a category with its own topic goes there
    if (thread) base.message_thread_id = Number(thread);
    let res = await post(url, { ...base, text: `<b>${esc(m.title)}</b>\n${esc(m.lines.join("\n"))}`.slice(0, 4000), parse_mode: "HTML" });
    if (res.status === 400) res = await post(url, { ...base, text: [m.title, ...m.lines].join("\n").slice(0, 4000) }); // markup Telegram refuses goes again as plain text
    return res.ok;
  },
  async wecom(env, m) {
    return coded(await post(`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${env.QYWX_KEY}`, { msgtype: "text", text: { content: plain(m) } }), "errcode");
  },
  async feishu(env, m) {
    const ts = String(Math.floor(Date.now() / 1000)); // Feishu signs an empty message with "<seconds>\n<secret>" as the key
    const signed = env.FSSECRET ? { timestamp: ts, sign: await sign(`${ts}\n${env.FSSECRET}`, "") } : {};
    return coded(await post(`${(env.FS_HOST || "https://open.feishu.cn").replace(/\/+$/, "")}/open-apis/bot/v2/hook/${env.FSKEY}`, { msg_type: "text", content: { text: plain(m) }, ...signed }), "code");
  },
  async dingtalk(env, m) {
    const ts = String(Date.now()); // DingTalk signs "<milliseconds>\n<secret>" with the secret as the key
    const signed = env.DD_BOT_SECRET ? `&timestamp=${ts}&sign=${encodeURIComponent(await sign(env.DD_BOT_SECRET, `${ts}\n${env.DD_BOT_SECRET}`))}` : "";
    return coded(await post(`https://oapi.dingtalk.com/robot/send?access_token=${env.DD_BOT_TOKEN}${signed}`, { msgtype: "text", text: { content: plain(m) } }), "errcode");
  },
  async webhook(env, m) {
    return (await post(env.WEBHOOK_URL, { title: m.title, body: m.lines.join("\n"), level: m.loud ? "alert" : "info", category: m.cat || "", source: "infra-alerts" })).ok;
  },
};
const CONFIGURED = { telegram: (e) => e.TG_BOT_TOKEN && e.TG_CHAT_ID, wecom: (e) => e.QYWX_KEY, feishu: (e) => e.FSKEY, dingtalk: (e) => e.DD_BOT_TOKEN, webhook: (e) => e.WEBHOOK_URL };

// To every configured channel → true when at least one took it. What failed is logged by name only, never with a token.
export async function send(env, m) {
  const names = Object.keys(CHANNELS).filter((c) => CONFIGURED[c](env));
  if (!names.length) console.warn("infra-alerts: no channel configured (set the TG_BOT_TOKEN and TG_CHAT_ID secrets)");
  const took = await Promise.all(names.map((c) => CHANNELS[c](env, m).catch(() => false)));
  took.forEach((ok, i) => ok || console.warn(`infra-alerts: ${names[i]} did not take the message`));
  return took.some(Boolean);
}

// ---------- state: D1 table kv, rows "state" (the alerts) and "hb:<host>" (its last heartbeat) ----------
const TABLE = "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)";
async function load(env) {
  const [, rows] = await env.DB.batch([env.DB.prepare(TABLE), env.DB.prepare("SELECT k, v FROM kv")]);
  return Object.fromEntries(rows.results.map((r) => [r.k, r.v]));
}
const save = (env, k, v) => env.DB.batch([env.DB.prepare(TABLE), env.DB.prepare("INSERT INTO kv (k, v) VALUES (?1, ?2) ON CONFLICT (k) DO UPDATE SET v = ?2").bind(k, v)]);

function local(cfg, now) { // the day and hour where the team lives
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: cfg.timezone || "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(now * 1000)).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, hour: Number(p.hour) };
}

// One cron run: read, compare with the last run, send what changed. A message no channel took is kept for the next run.
export async function tick(env, now = Math.floor(Date.now() / 1000), cfg = config) {
  const rows = await load(env), st = rows.state ? JSON.parse(rows.state) : {};
  const readings = [...(await Promise.all((cfg.probes || []).map(probe))), ...heartbeats(cfg, rows, now)];
  const notices = evaluate(st, readings, now, cfg);
  const queue = st.outbox || [];
  if (notices.length) queue.push(compose(cfg.name, notices, cfg.labels));
  const { day, hour } = local(cfg, now);
  if (cfg.dailyReportHour != null && st.reportDay !== day && hour >= cfg.dailyReportHour) { st.reportDay = day; queue.push(report(cfg.name, readings, cfg.labels)); }
  st.outbox = [];
  for (const m of queue) if (!(await send(env, m))) st.outbox.push(m);
  st.outbox = st.outbox.slice(-20);
  st.lastRun = now;
  await save(env, "state", JSON.stringify(st));
  // Nothing inside Cloudflare can tell you this Worker stopped: an outside dead man's switch (healthchecks.io, …) can.
  if (env.SELF_PING_URL) await fetch(env.SELF_PING_URL, { method: "POST", signal: AbortSignal.timeout(5000) }).catch(() => {});
  return { readings, notices };
}

function authorized(request, token) {
  const enc = new TextEncoder(), given = enc.encode(request.headers.get("Authorization") || ""), want = enc.encode(`Bearer ${token}`);
  return !!token && given.byteLength === want.byteLength && crypto.subtle.timingSafeEqual(given, want);
}

export async function handle(request, env, cfg = config, now = Math.floor(Date.now() / 1000)) {
  const { pathname } = new URL(request.url);
  if (request.method === "GET" && pathname === "/health") {
    const st = JSON.parse((await load(env)).state || "{}"), fresh = st.lastRun && now - st.lastRun < 300;
    return new Response(fresh ? "ok" : "stale", { status: fresh ? 200 : 503 });
  }
  if (request.method === "GET" && pathname === "/incidents") { // for agents; its own token, so a host's token cannot read the others
    if (!authorized(request, env.AGENT_TOKEN)) return new Response("unauthorized", { status: 401 });
    return Response.json({ now, incidents: incidents(cfg, await load(env), now) });
  }
  if (request.method !== "POST") return new Response("not found", { status: 404 });
  if (!authorized(request, env.HEARTBEAT_TOKEN)) return new Response("unauthorized", { status: 401 });
  const host = /^\/hb\/([\w.-]{1,64})$/.exec(pathname)?.[1];
  if (host && (cfg.heartbeats || []).some((h) => h.name === host)) {
    const body = await request.json().catch(() => ({}));
    const alerts = (Array.isArray(body?.alerts) ? body.alerts : []).slice(0, 30) // what the host says is open on it, kept for /incidents
      .map((a) => ({ key: String(a?.key).slice(0, 100), level: a?.level === CRIT ? CRIT : "warn", text: String(a?.text).slice(0, 200), since: Number(a?.since) || now }));
    await save(env, `hb:${host}`, JSON.stringify({ at: now, open: Number(body?.open) || 0, crit: Number(body?.crit) || 0, alerts }));
    return new Response("ok");
  }
  if (pathname === "/test") {
    const took = await send(env, { title: `${PREFIX} ✅ ${cfg.name}: test message`, lines: ["If you can read this, alerts from the Cloudflare Worker will reach you here."], loud: true });
    return new Response(took ? "sent" : "no channel took it", { status: took ? 200 : 502 });
  }
  return new Response("not found", { status: 404 });
}

export default {
  fetch: (request, env) => handle(request, env),
  scheduled: (event, env, ctx) => ctx.waitUntil(tick(env)),
};
