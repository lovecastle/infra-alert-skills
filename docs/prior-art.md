# Prior art: what we read and what we took

Before generalising our own setup we read how others solve the same problem: international open-source monitors,
the Chinese 探针 (probe) ecosystem and its notification channels, and existing agent skills. Every project below
was opened in November 2026. This is a reading list with credit, not a ranking; where we could not confirm
something first-hand, it says so.

## Monitors and alerting tools

| Project | Shape | What we learned from it |
|---|---|---|
| [Uptime Kuma](https://github.com/louislam/uptime-kuma) | Pull probes plus a push monitor type | A "pending" state while retrying before calling something down; nothing sent on the first good beat; maintenance that suppresses notifications |
| [Gatus](https://github.com/TwiN/gatus) | Probes from one YAML file, plus push endpoints | Its documented defaults shaped ours: failure threshold 3, success threshold 2; reminders off unless asked; heartbeats for things that push |
| [Healthchecks](https://github.com/healthchecks/healthchecks) | Dead man's switch | Period plus grace; "new" is not "down"; a job pings at its end (`cmd && curl …`); downtime computed from the stored transition |
| [Beszel](https://github.com/henrygd/beszel) | Hub plus a tiny agent per host | That an agent can be tiny; its note that polling `nvidia-smi` can keep an idle GPU out of power saving ([GPU guide](https://beszel.dev/guide/gpu)) |
| [Netdata](https://learn.netdata.cloud) | Agent per host | Hysteresis: warn at 85, clear at 75. An open alert should not clear on the line it opened at |
| [Monit](https://mmonit.com/monit/documentation/monit.html) | Local agent | "For N cycles" before alerting; one alert on failure and one on recovery; reminders as an option |
| [Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/) | Router | Grouping into one notification; repeat interval of 4 hours; "resolved" messages; HTML parse mode for Telegram |
| [UptimeFlare](https://github.com/lyc8503/UptimeFlare) | Uptime checks on Cloudflare Workers | Proof that Workers are enough for this; a grace period before alerting; that it moved its state from KV to D1. Its security advisory about monitor config reaching the client is why our config holds no secrets |
| [cf-workers-status-page](https://github.com/eidam/cf-workers-status-page) | Workers cron plus KV | A cautionary tale about free-plan limits: subrequests cap how many monitors one run can check |
| [server-pulse](https://github.com/volkv/server-pulse) | A bash oneshot on a systemd timer | The closest in spirit: no daemon; warn/critical/resolved; a `silence` that keeps updating state |
| [awesome-prometheus-alerts](https://github.com/samber/awesome-prometheus-alerts) | Alert rule collection | Sensible starting thresholds for hosts and GPUs, and which Xid codes are fatal |
| [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter), [nvitop](https://github.com/XuehaiPan/nvitop) | GPU metrics | Which `nvidia-smi` fields are worth reading |
| [Apprise](https://github.com/caronc/apprise), [ntfy](https://docs.ntfy.sh/publish/) | Notification library, push service | One destination per URL or key; priorities; that a message has a size limit everywhere |

## The Chinese ecosystem

| Project | Shape | What we learned from it |
|---|---|---|
| [哪吒监控 Nezha](https://github.com/nezhahq/nezha) | Dashboard plus agent | Rules as type/min/max/duration; alerting only when out of range for most of a window; notification as a templated HTTP request, which is why we have a generic webhook |
| [Komari](https://github.com/komari-monitor/komari) | Server plus lightweight agent | A "test" event as a first-class thing: every setup should end with a test message |
| [ServerStatus](https://github.com/cppla/ServerStatus), [ServerStatus-Rust](https://github.com/zdz/ServerStatus-Rust) | Agent pushes to a server | A cooldown between repeats; certificate expiry alerts at fixed days |
| [青龙 Qinglong](https://github.com/whyour/qinglong) | Cron panel | The `sendNotify` contract: one function, every configured channel gets the message, unconfigured ones are skipped, keys overridable by environment. We reuse its key names (`TG_BOT_TOKEN`, `QYWX_KEY`, `DD_BOT_TOKEN`, `FSKEY`, `BARK_PUSH`, `PUSH_KEY`, `PUSH_PLUS_TOKEN`, …) so an existing config pastes in |
| [gpu-monitor](https://github.com/reacher-z/gpu-monitor), [gpu_lurker](https://github.com/RenShuhuai-Andy/gpu_lurker) | Single-file GPU watchers | What people running training jobs actually want to hear: the job crashed, the card is idle, temperature, memory, a card disappeared |
| Aliyun PAI's guide to GPU monitoring with an agent ([doc](https://help.aliyun.com/zh/pai/use-cases/monitoring-gpus-with-openclaw)) | A cron script pushing to DingTalk | Idle utilisation as "the job stalled"; a daily report at a fixed hour |

Channels people there actually use, and their documented limits, are in
[other-channels.md](../skills/setup-alert-channel/references/other-channels.md). The tightest ones set our
defaults: plain text, about 1900 bytes, one message per run, a fixed word (`infra`) in every title so keyword
security passes.

## Agent skills and plugins

| Project | What it is | What we took, or what was missing |
|---|---|---|
| [Agent Skills specification](https://agentskills.io/specification), [Anthropic's skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | The format | `SKILL.md` under 500 lines with detail pushed to `references/`, scripts in `scripts/`, descriptions in the third person that say when to use the skill |
| [Claude Code plugins](https://code.claude.com/docs/en/plugins-reference) and [marketplaces](https://code.claude.com/docs/en/plugin-marketplaces) | Packaging | One tree that installs three ways: plugin marketplace, `npx skills add`, zip upload |
| [vercel-labs/skills](https://github.com/vercel-labs/skills) | The `npx skills` installer | Why the skills live in `skills/<name>/` and are self-contained |
| [Official Telegram channel plugin](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram) | Two-way chat with a running Claude session | A different job: it needs a live session. Alerts must arrive when no agent is running |
| [sanjay3290/ai-skills telegram](https://github.com/sanjay3290/ai-skills/blob/main/skills/telegram/SKILL.md) | A send-only Telegram skill in bash | Secrets in a mode-600 file overridden by environment variables |
| [cloudflare/skills](https://github.com/cloudflare/skills) | Cloudflare's own build-focused skills | A model for layout. Nothing in it about monitoring or alerting |
| [ahmedasmar/devops-claude-skills](https://github.com/ahmedasmar/devops-claude-skills) | A marketplace of DevOps skills | Good structure (scripts, references); oriented to Prometheus and SLOs rather than installing anything |
| [oikewll/skill-monitor](https://github.com/oikewll/skill-monitor) | A small host-check skill with thresholds and a cooldown | The closest in shape; single host, no GPU, no external watcher |

What we did not find: a skill set that covers hosts, GPUs and an outside watcher, alerts on its own without an
agent session running, and has the agent install and verify it. Existing work was advisory (it explains
monitoring), send-only (it posts a message), or tied to a live session. That gap is what this repository is for.

Not verified first-hand: the ClawHub and Tencent SkillHub registries did not render for us, so skills listed only
there (several "server monitor" entries) are not assessed here.

## Categories, and agents that act from a chat

Read in November 2026 for version 0.2 (alert categories and `run-ops-agents`).

| Source | What we learned from it |
|---|---|
| [Google SRE book](https://sre.google/sre-book/introduction/), [OpenShift alerting consistency](https://github.com/openshift/enhancements/blob/master/enhancements/monitoring/alerting-consistency.md), [Prometheus alerting practices](https://prometheus.io/docs/practices/alerting/) | Three kinds of output: act now, act soon, read later. No page where there is nothing to do. Our URGENT / ATTENTION / STATS |
| [Flashduty](https://docs.flashduty.com/zh/flashduty/what-is-incident), [HertzBeat](https://hertzbeat.apache.org/zh-cn/docs/help/alert_threshold) | Severity is separate from state (待处理 → 处理中 → 已关闭); an incident nobody claims in time escalates. The Chinese words for the levels differ between products, so ours are configurable |
| [Telegram Bot API changelog](https://core.telegram.org/bots/api-changelog), [bot features](https://core.telegram.org/bots/features), [FAQ](https://core.telegram.org/bots/faq) | Bots can see some messages of other bots since Bot API 10.0 (May 2026), behind a setting, and the FAQ still says they cannot. We do not depend on it: agents read incidents from the Worker and hand work to each other in-process. Privacy mode must be off for a bot to see mentions reliably. The clicker of a button is `callback_query.from.id` |
| [HolmesGPT](https://github.com/robusta-dev/holmesgpt) and its [bash toolset](https://holmesgpt.dev/latest/data-sources/builtin-toolsets/bash/) | Read-only by design; an allowlist of command prefixes; `sudo` blocked. Ours: the allowlist lives on the server, in sudoers |
| [incident.io investigations](https://docs.incident.io/investigations/trust-and-safety), [PagerDuty SRE agent](https://docs.pagerduty.com/ai-automation/advance/sre-agent), [Rootly](https://rootly.com/ai-sre) | Commercial agents recommend and wait for a person; unprompted fixes are off by default. An agent can be a level in the escalation chain |
| [华为云 CCE + 飞书](https://support.huaweicloud.com/skill-cce/cce_skill_0008.html) | The closest to ours: read-only triage, then options with scope, rollback and verification, executed only after an explicit confirmation in the chat |
| OpenClaw [groups](https://docs.openclaw.ai/channels/groups), [multi-agent](https://docs.openclaw.ai/concepts/multi-agent), [approvals](https://docs.openclaw.ai/tools/exec-approvals-advanced) | One bot account per agent; agents talk inside the gateway, not through the chat; approvers are a list of numeric ids, separate from who is in the chat; a prompt that times out is a no |
| [Claude Code channels](https://code.claude.com/docs/en/channels), [headless mode](https://code.claude.com/docs/en/headless) | Channels feed a live session, and anyone who can reply can approve: not what an unattended agent needs. Headless `claude -p` with a JSON schema, an allowlist of tools and `--resume` is |
| [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram), [ccbot](https://github.com/six-ddc/ccbot), [cc-connect](https://github.com/chenhg5/cc-connect) | Allowlisted user ids, tool allowlists, rate and cost caps, an audit log, a session per thread, a separate OS user. Also what to avoid: running the agent with permissions skipped |

What these agree on, and what `run-ops-agents` does:

1. Diagnosis never changes anything.
2. Changes come from a short allowlist; everything else needs a person's yes, and the proposal says scope, undo and check.
3. Who may approve is a list of ids, checked on the button press. Being in the chat grants nothing.
4. No answer is a no, and then a person is called.
5. Chat text, alert text and logs are data, not instructions; only listed senders can start an agent.
6. Each agent has its own bot and account; agents do not take orders from bots.
7. Every proposal, approval and run is logged; the report says what is uncertain.

## The rules we ended up with

Taken together, the projects above agree on more than they differ:

1. Confirm before telling: several failures in a row, not one.
2. Tell changes, not states; group what changed into one message.
3. Say when it is over, and how long it lasted.
4. Clear below where you opened (hysteresis), or it flaps.
5. Repeat only what is critical, and rarely.
6. Give people a silence that expires and still watches.
7. Something outside must notice silence: a heartbeat with a grace period.
8. End every setup with a test message.
9. Keep secrets out of anything that can be read, shown or shipped.
10. Design to the tightest channel limit: short plain text, few messages.
