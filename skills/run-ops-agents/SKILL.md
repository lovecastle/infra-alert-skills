---
name: run-ops-agents
description: Puts AI agents into the team's Telegram alert group so that critical alerts are worked on even when no admin has reacted. Installs opsagent.py, a daemon that runs one or more agents (each a Telegram bot). A lead agent watches the open incidents and hands each to the agent that knows that system; agents diagnose over SSH with Claude Code in headless mode, fix what a written policy allows, ask an admin for approval with buttons in the chat before anything else, and report in the chat. Admins can mention or reply to any agent to give orders or discuss. Use when the user wants an agent or bot to watch the alert channel, handle alerts automatically, respond when tagged in the group, have several agents with a lead that delegates, or approve fixes from the chat. Triggers include "agent in the telegram group", "auto-handle alerts", "on-call agent", "agent tự xử lý cảnh báo", "tag agent trong nhóm", "lead agent giao việc", "自动处理告警", "群里@机器人", "值班 agent".
license: MIT
---

# Agents in the alert chat

The monitors tell the chat. This skill adds someone who is always there to read it: agents that pick up a critical
alert no admin has taken, work on it within a written policy, and answer when an admin talks to them.

```
host monitors ─┐                                   ┌─ lead ── announces, waits for an admin, hands over
Worker probes ─┴─► Worker GET /incidents ─► opsagent ├─ web  ── diagnoses over SSH, fixes or asks, reports
admins in the group ── @mention / reply / buttons ─►  └─ gpu  ── …
```

One process (`opsagent.py`) runs all the agents. It costs nothing while idle: Claude Code is started (`claude -p`)
only when there is an incident or an order, once per piece of work.

## What decides what

| Decided in code (cannot be talked out of) | Decided by the model, under the policy text |
|---|---|
| Who may give orders and approve: the Telegram user ids in `admins`. Being in the group gives no say | What the cause is, and what to do |
| Which chat is listened to; messages from bots are ignored | Whether a step is covered by `auto` or needs approval |
| The wait for a human before an agent starts (`grace_minutes`) | The text of proposals and reports |
| A proposal runs only after an admin presses Approve; no answer in `approval_minutes` means no | |
| At most `max_per_hour` incidents are started on; `/pause` stops pickup | |
| What Claude Code may run on the agent machine (`allowed_tools`) | |

The model decides whether something needs approval. **What an agent is able to do on a server is decided on that
server**, by the account it logs in with: read [references/access.md](references/access.md) before giving agents
access. Do not give them root.

## Before you start

- The alert channel is a **Telegram group** (`setup-alert-channel`), and `monitor-hosts` and `monitor-cloudflare`
  are installed: the Worker is where agents read what is open. Without the Worker, agents still answer mentions.
- An always-on Linux machine for the agents, preferably not one of the servers they look after (an agent on a
  server that is down cannot help). Python 3.8+, Node.js for Claude Code.
- A Claude plan or API key the agents may use. Each piece of work is one Claude Code session.

## Install

1. **Agree on the setup with the user** before touching anything, and write it down in the reply:
   - the agents. Start with one, the lead. Add an agent per area only when the areas are really different (web
     servers, a GPU machine, a database);
   - the admins: Telegram user ids (each person writes to `@userinfobot`, or run `infrawatch.py chats`);
   - the policy: what agents may do without asking, what needs a yes. Read the defaults in
     [agents.example.ini](assets/agents.example.ini) aloud and let the user change them;
   - `grace_minutes`: how long admins have before an agent starts (default 5).

2. **A bot per agent.** The user does this in Telegram with `@BotFather`: `/newbot` for each agent, then
   `/setprivacy` → choose the bot → **Disable** (otherwise the bot does not see the chat or its mentions), then add
   each bot to the group. A bot added before privacy was disabled must be removed and added again. Tokens go
   straight into a staging file, never into the conversation:

   ```bash
   umask 077; mkdir -p ~/.infra-alerts; ${EDITOR:-nano} ~/.infra-alerts/agents.env
   # LEAD_BOT_TOKEN=...      one line per agent: <NAME>_BOT_TOKEN
   ```

3. **Open the incidents feed.** The Worker and the hosts must be at version 0.2 of this repository's files (the
   heartbeat carries the open alerts; the Worker has `/incidents`). Redeploy the Worker from
   `monitor-cloudflare/assets/worker` and copy the new `infrawatch.py` to each host if they are older. Then:

   ```bash
   T=$(openssl rand -hex 32); echo "AGENT_TOKEN=$T" >> ~/.infra-alerts/agents.env
   cd infra-alerts && printf %s "$T" | npx wrangler secret put AGENT_TOKEN; unset T
   . ~/.infra-alerts/agents.env; curl -s -H "Authorization: Bearer $AGENT_TOKEN" https://infra-alerts.ACCOUNT.workers.dev/incidents
   ```

   The answer is `{"now":…,"incidents":[…]}`: everything open right now.

4. **Prepare the agent machine** (as root there):

   ```bash
   useradd --system --create-home --shell /bin/bash opsagent
   mkdir -p /var/lib/opsagent/work/.claude/skills && chown -R opsagent: /var/lib/opsagent
   sudo -u opsagent ssh-keygen -t ed25519 -N "" -f /home/opsagent/.ssh/id_ed25519
   ```

   Install Claude Code there by its own instructions (https://code.claude.com/docs/en/setup) until
   `sudo -u opsagent claude --version` answers. Copy the `triage-alerts` skill folder to
   `/var/lib/opsagent/work/.claude/skills/triage-alerts` so agents follow it. Signing Claude Code in is the user's step: they run `claude setup-token` on any machine where they are
   logged in and put the result in `agents.env` as `CLAUDE_CODE_OAUTH_TOKEN=…` (or `ANTHROPIC_API_KEY=…`).

5. **Give the agents their access to each server**, following [references/access.md](references/access.md): an
   `opsagent` account with the public key from step 4 and a short sudoers list. Add an entry per server to
   `/home/opsagent/.ssh/config` whose `Host` is the name the monitor uses for it (`vps-01`), so `ssh vps-01` works.
   Check: `sudo -u opsagent ssh vps-01 uptime`.

6. **Install and check.**

   ```bash
   scp scripts/opsagent.py assets/agents.example.ini assets/opsagent.service ~/.infra-alerts/agents.env AGENTHOST:/tmp/
   ssh AGENTHOST 'sudo install -D -m 755 /tmp/opsagent.py /opt/opsagent/opsagent.py &&
     sudo install -D -m 640 -g opsagent /tmp/agents.example.ini /etc/opsagent/agents.ini &&
     sudo install -m 600 -o opsagent /tmp/agents.env /etc/opsagent/agents.env &&
     sudo install -m 644 /tmp/opsagent.service /etc/systemd/system/ && rm /tmp/agents.env /tmp/agents.example.ini'
   ```

   Edit `/etc/opsagent/agents.ini` (chat id, admins, the incidents URL, the agents and their hosts, the policy,
   the language), then:

   ```bash
   sudo -u opsagent python3 /opt/opsagent/opsagent.py check    # every line should be ✅; nothing is posted
   sudo -u opsagent python3 /opt/opsagent/opsagent.py test     # each agent says hello in the group
   sudo systemctl daemon-reload && sudo systemctl enable --now opsagent && journalctl -u opsagent -f
   ```

7. **Prove it with the user watching the chat.** Cause a harmless critical alert on one host, as `monitor-hosts`
   does (`[tcp] selftest = 127.0.0.1:9`, a port nothing listens on):
   - the alert arrives, then the lead's notice with two buttons. Press **I take it**: no agent starts. Good;
   - remove and add the check again, and this time wait: after the grace time the agent reports what it found
     (a check that points at a closed port) and that there is nothing to fix;
   - write `@<agent> what is the load on vps-01 right now?` and get an answer;
   - delete the selftest line.

   Only after this has been seen is the setup done. Tell the user about `/pause`.

## Day to day, in the chat

| An admin does | What happens |
|---|---|
| Nothing, for `grace_minutes` after a critical alert | The agent for that host starts, edits the notice to say so, and reports |
| Presses **I take it** | Agents stay out of that incident |
| Presses **Start now** | The agent starts without waiting |
| `@agent <order or question>` | That agent works on it and replies. It sees the recent talk of admins and agents |
| Replies to an agent's message | The same piece of work goes on, with what was said |
| Presses **Approve** / **Refuse** on a proposal | Approve: the agent does exactly what it proposed and reports. Refuse: it stops |
| Replies in words to a proposal | The proposal is off; the words are the new order |
| `@agent /status` | What each agent is doing, and the incidents |
| `@agent /pause`, `/resume` | Agents stop, or resume, picking up incidents. Mentions always work |
| `@agent /stop` | That agent's running work is ended |

An agent's messages carry `#agent`. An incident still open `recheck_minutes` after an agent reported it done is
handed back loudly, once: the fix did not hold and a person is needed.

Every decision is a line in `/var/lib/opsagent/audit.jsonl` and in the journal: who asked, which agent, what came
of it, what it cost.

## When it does not work

| Symptom | Cause |
|---|---|
| A bot does not react to its @mention | Privacy mode is on (`check` says so): BotFather → `/setprivacy` → Disable, remove the bot from the group and add it again |
| `getUpdates: HTTP 409` in the journal | A second process is using that bot's token |
| "I could not work on this: Failed to authenticate" | Claude Code is not signed in for the `opsagent` user: `CLAUDE_CODE_OAUTH_TOKEN` in `agents.env` |
| `check` says incidents `HTTP 401` | `AGENT_TOKEN` differs between `agents.env` and the Worker's secret |
| An agent says a command was refused | It is outside `allowed_tools` on the agent machine, or outside its sudoers on the server. Widen on purpose, or leave it |
| Agents never start on an alert | Warnings are not picked up (`levels = crit`); `/pause` is on; or the host runs an `infrawatch.py` older than 0.2 |

## Limits

- Agents work on Telegram only. The other channels still get every alert.
- The daemon's own short notices are in English; what agents write is in `language`.
- Two processes cannot share a bot. Agents on a second machine need their own bots and their own config.
- Telegram bots do not read each other reliably, so agents hand work to each other inside the process and only
  show it in the chat. An agent never acts on a message from a bot.
