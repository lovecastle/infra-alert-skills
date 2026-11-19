# Limits, cost and design of the Worker

## What it uses on the free plan

Figures from Cloudflare's documentation, read in November 2026. Check the linked pages before relying on them.

| Resource | Free plan limit | What the Worker uses |
|---|---|---|
| Cron Triggers | finest schedule every minute; 5 per account | 1 |
| Subrequests per run | 50 | 1 per probe, 1 per channel per message, 1 for `SELF_PING_URL` → keep probes under about 40 |
| CPU per cron run | 10 ms | far less: the run is almost all waiting on the network, which is not CPU time |
| D1 rows written | 100,000 a day | one upsert a run and one per heartbeat: 1,440 a day each, counted once or twice by D1 (the row and its key index) |
| D1 rows read | 5,000,000 a day | a handful per run and per `/health` request |
| Worker requests | 100,000 a day | 1 per heartbeat, plus `/health` checks |

Ten hosts pinging every minute is 16,000 to 32,000 rows written a day: well inside the free plan. KV would not be:
its free plan allows 1,000 writes a day, fewer than one host's heartbeats, which is why the state is in D1.

Sources: <https://developers.cloudflare.com/workers/platform/limits/>,
<https://developers.cloudflare.com/workers/configuration/cron-triggers/>,
<https://developers.cloudflare.com/d1/platform/pricing/>, <https://developers.cloudflare.com/kv/platform/limits/>

## Telegram's limits

About 20 messages a minute to one group, and 4096 characters a message
(<https://core.telegram.org/bots/faq>). The Worker sends one message per run with everything that changed, and
cuts it at 4000 characters.

## How it decides

- **Probes**: a GET from Cloudflare's network with a 10-second timeout. Fine = 2xx/3xx (redirects are followed), or
  exactly `status` if given, and the body contains `contains` if given. `failThreshold` failures in a row open an
  alert; `okThreshold` successes close it. One slow minute or one edge hiccup tells nobody.
- **Heartbeats**: the Worker stores when each host last called `POST /hb/<name>`. Silent longer than `graceMinutes`
  = down; the next ping = back, with how long it was gone. A host that has never pinged is "waiting", not down, so
  adding a host to the config before installing its agent does not alert.
- **Messages**: only changes. A critical alert still open is repeated every `remindHours`. Recoveries and the daily
  report are sent without sound.
- **A send that fails** (Telegram down, a wrong token) is kept in the state and sent with the next run, up to 20
  messages.
- **State**: one D1 table `kv`: row `state` (open alerts, counters, the outbox, when the cron last ran) and one
  row `hb:<host>` per host. It is created on first use; there is no migration to run.

## Watching the watcher

Nothing inside Cloudflare tells you that a Worker's cron stopped. Three layers, pick at least one:

1. Each host checks `GET /health` (step 7 of the skill): it turns 503 when the cron has been silent 5 minutes, and
   the host alerts through its own channel config.
2. `npx wrangler secret put SELF_PING_URL` with the ping URL of a dead man's switch service: the Worker calls it
   at the end of every run, and that service alerts you when the calls stop.
3. The daily report: if it did not come, look.

## Probing your own zone

`wrangler.toml` sets the `global_fetch_strictly_public` compatibility flag, so a probe of a hostname on your own
Cloudflare zone goes through Cloudflare's front door like a visitor's request, not straight to the origin
(<https://developers.cloudflare.com/workers/configuration/compatibility-flags/>). Two consequences: your WAF and
bot rules apply to the probe, and a probe must never point at a URL routed to this Worker, or it would call itself.

## What it does not do

- No status page and no history: it tells a chat, and D1 holds only the current state.
- No checks of Cloudflare analytics (5xx rate, Worker errors, quota use). See the case study in the repository's
  `docs/` for how one team does that with the GraphQL Analytics API.
- One region's view: probes run wherever Cloudflare schedules the cron. A site down in one country only is not
  seen.
