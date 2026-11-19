// What this Worker watches. Edit, then: npx wrangler deploy
// Secrets (bot token, chat id, heartbeat token) are NOT here: they are set with `npx wrangler secret put <NAME>`.
export default {
  name: "my-infra",        // shown in every message
  timezone: "UTC",         // IANA name, for the daily report hour: "Asia/Ho_Chi_Minh", "Asia/Shanghai", "Europe/Berlin"
  dailyReportHour: 8,      // local hour of the daily "everything I watch" message; null = none
  failThreshold: 2,        // bad minutes in a row before a probe alerts
  okThreshold: 2,          // good minutes in a row before it is called back up
  remindHours: 4,          // say again while something is still down; 0 = never
  // The word in a message's title for each category, in your language. The tags (#urgent, …) stay the same.
  // labels: { urgent: "URGENT", resolved: "RESOLVED", stats: "STATS" },

  // URLs asked every minute from Cloudflare's network. Free plan: 50 subrequests a run, so keep this under ~40.
  probes: [
    // { name: "site", url: "https://example.com/" },                                   any 2xx/3xx answer is fine
    // { name: "api", url: "https://api.example.com/health", contains: "ok" },          ...and the body must contain this
    // { name: "login", url: "https://example.com/login", status: 200, timeoutMs: 5000 },
  ],

  // Hosts running infrawatch.py with  [heartbeat] url = https://<this worker>/hb/<name>  — down when silent this long.
  heartbeats: [
    // { name: "vps-01", graceMinutes: 5 },
  ],
};
