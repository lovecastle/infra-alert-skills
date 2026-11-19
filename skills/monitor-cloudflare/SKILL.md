---
name: monitor-cloudflare
description: Deploys a small Cloudflare Worker (Cron Trigger and D1, free plan) that watches from outside the servers. Every minute it probes URLs from Cloudflare's network, and it expects a heartbeat from each host running infrawatch, alerting a chat (Telegram, WeCom, Feishu, DingTalk, webhook) when a site goes down or a host goes silent, and again when it recovers. Also covers Cloudflare's own notifications for Tunnels, Pages and certificates. Use when the user wants uptime monitoring, an external watcher or dead man's switch for servers, heartbeat monitoring, alerts for sites or APIs behind Cloudflare, or to know when a VPS is completely down. Triggers include "uptime monitor", "is my site down", "heartbeat", "dead man's switch", "cloudflare worker cron", "giám sát uptime", "theo dõi website", "máy chủ sập", "网站监控", "心跳检测", "拨测".
license: MIT
compatibility: Needs Node.js 18+ and a Cloudflare account (free plan is enough) where the agent runs.
---

# Monitor from Cloudflare

A machine that is off cannot say so. This Worker is the watcher that lives somewhere else: it asks your URLs every
minute and notices when a host stops sending heartbeats. The template is in `assets/worker/`; paths are relative
to this skill's folder.

| Path | What |
|---|---|
| `assets/worker/src/config.js` | what to watch: probes, hosts, time zone, thresholds. No secrets |
| `assets/worker/src/index.js` | the Worker |
| `assets/worker/wrangler.toml` | name, cron, D1 binding |

Endpoints of the deployed Worker: `POST /hb/<host>` (heartbeat, needs the token), `POST /test` (send a test message,
needs the token), `GET /health` (200 while the cron runs, 503 once it has been silent 5 minutes).

## Ground rules

- Deploying creates resources in the user's Cloudflare account: say what will be created (one Worker, one D1
  database) and get a yes first.
- Secrets go in with `wrangler secret put`, read from `~/.infra-alerts/notify.env`. Never into `config.js`,
  `wrangler.toml`, the repository, or a message. If no channel exists yet, do `setup-alert-channel` first.
- `npx wrangler login` opens a browser: the user does that step.

## Deploy

1. **Copy the template** to a folder of the user's choosing (it becomes their project; suggest a private repository):

   ```bash
   cp -r assets/worker ~/infra-alerts-worker && cd ~/infra-alerts-worker
   npx wrangler whoami || npx wrangler login
   ```

2. **Create the database** and put its id in `wrangler.toml` (`database_id`). The table is made on first use.

   ```bash
   npx wrangler d1 create infra-alerts
   ```

3. **Edit `src/config.js`** with the user: `name`, `timezone`, the URLs under `probes` (public pages and health
   endpoints, as a visitor reaches them), and one entry under `heartbeats` for each host that runs infrawatch.
   Do not probe a URL routed to this Worker itself.

4. **Deploy**, and note the URL it prints (`https://infra-alerts.<account>.workers.dev`):

   ```bash
   npx wrangler deploy
   ```

5. **Secrets** (after the first deploy, so the Worker exists; they take effect at once). Make a heartbeat token,
   keep it with the others, load them:

   ```bash
   grep -q '^HEARTBEAT_TOKEN=' ~/.infra-alerts/notify.env || echo "HEARTBEAT_TOKEN=$(openssl rand -hex 24)" >> ~/.infra-alerts/notify.env
   for k in TG_BOT_TOKEN TG_CHAT_ID HEARTBEAT_TOKEN; do
     grep "^$k=" ~/.infra-alerts/notify.env | cut -d= -f2- | tr -d '\n' | npx wrangler secret put "$k"
   done
   ```

   Other channels use the same names as in `notify.env`: `TG_THREAD_ID`, `TG_API_HOST`, `QYWX_KEY`, `FSKEY`,
   `FSSECRET`, `FS_HOST`, `DD_BOT_TOKEN`, `DD_BOT_SECRET`, `WEBHOOK_URL`. Add each one that is set to the loop.
   A Telegram topic per category of message: `TG_THREAD_URGENT`, `TG_THREAD_RESOLVED`, `TG_THREAD_STATS` (the
   words in the titles are `labels` in `config.js`). `AGENT_TOKEN` opens `GET /incidents`, the list of everything
   open that the `run-ops-agents` skill reads; without that secret the endpoint answers 401.

6. **Verify**, in this order:

   ```bash
   W=https://infra-alerts.ACCOUNT.workers.dev; . ~/.infra-alerts/notify.env
   curl -sS -X POST -H "Authorization: Bearer $HEARTBEAT_TOKEN" "$W/test"     # "sent", and the user sees it in the chat
   curl -sS -o /dev/null -w '%{http_code}\n' "$W/health"                       # 200 once the cron has run
   npx wrangler tail --format pretty                                           # watch a run; Ctrl-C to stop
   ```

   `/health` says 503 until the first cron run. A new or changed Cron Trigger can take up to 15 minutes to start.
   If `dailyReportHour` has passed today, the first run sends the daily report, listing everything watched.

7. **Connect the hosts.** On each host that runs infrawatch add to `/etc/infrawatch/notify.env`:

   ```
   HEARTBEAT_URL=https://infra-alerts.ACCOUNT.workers.dev/hb/vps-01
   HEARTBEAT_TOKEN=<the same token>
   ```

   and to `/etc/infrawatch/config.ini`, so each host also watches the watcher:

   ```ini
   [http]
   watcher = https://infra-alerts.ACCOUNT.workers.dev/health
   ```

   The name after `/hb/` must match an entry in `heartbeats`. Within a minute `npx wrangler tail` shows the POST,
   and the next daily report lists the host as "heartbeat … ago".

8. **Prove it** (offer it): stop one host's timer for longer than its grace
   (`sudo systemctl stop infrawatch.timer`), wait for "🔴 vps-01: no heartbeat for 6m", start it again, wait for ✅.

## Changing it later

Edit `src/config.js`, `npx wrangler deploy`. A probe or host removed from the config is forgotten. Thresholds:
`failThreshold` bad minutes before a probe alerts (2), `okThreshold` good minutes before recovery (2),
`graceMinutes` of silence before a host is down (5: infrawatch pings every minute, so this tolerates a few missed
runs), `remindHours` between reminders while something is still down (4, 0 = never).

## Cloudflare's own notifications

For the parts of Cloudflare a probe cannot see, turn on the built-in notifications (dashboard → Notifications →
Add): **Tunnel Health Alert** (a `cloudflared` tunnel went down or degraded), **Pages** project updates (a failed
deployment), **Universal SSL** alerts, and **Incident Alerts** for Cloudflare's own outages. They deliver by email,
or to a webhook destination. Health Checks notifications need a paid zone plan; this Worker's probes do that job on
the free plan. List: <https://developers.cloudflare.com/notifications/notification-available/>

## When it does not work

| Symptom | Cause |
|---|---|
| `/test` answers `no channel took it` | A secret is missing or wrong. `npx wrangler secret list`; `npx wrangler tail` names the channel that refused |
| `/test` answers `unauthorized` | `HEARTBEAT_TOKEN` in the header is not the one stored in the Worker |
| `/health` stays 503 | The cron is not running: wait 15 minutes after the first deploy; check Workers → the Worker → Settings → Triggers. If the Worker is deployed by git push rather than `wrangler deploy`, cron changes may need `npx wrangler triggers deploy` |
| A probe of your own site fails from the Worker but works in a browser | A WAF or bot rule is challenging the Worker's request. Allow the User-Agent `infra-alerts-probe` for that path, or probe a health endpoint that has no challenge |
| A host shows `waiting for its first heartbeat` forever | Wrong URL or token on the host: `journalctl -u infrawatch -n 20` shows `heartbeat not taken: HTTP 401/404` |

Limits, cost and how the state is kept: [references/limits.md](references/limits.md).
