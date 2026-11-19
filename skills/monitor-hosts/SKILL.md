---
name: monitor-hosts
description: Installs and operates infrawatch, a one-file Python monitor with no dependencies, on Linux servers over SSH, so problems are told to a chat (Telegram, WeCom, Feishu, DingTalk and others) when they start and when they end. Watches disks, memory, load, systemd services, Docker containers, HTTP and TCP endpoints, TLS certificates, NVIDIA GPUs (temperature, memory, Xid errors, idle or dead jobs), reboots and OOM kills, with an optional security baseline (new SSH logins, changed auth files, new open ports). Use when the user wants to monitor a VPS, server or GPU machine, get alerts when a server or service goes down or a disk fills, watch an RTX or other NVIDIA card, or change, silence or remove such monitoring. Triggers include "monitor my VPS", "server alerts", "GPU monitoring", "nvidia-smi alerts", "giám sát VPS", "theo dõi máy chủ", "cảnh báo server", "服务器监控", "探针", "显卡监控".
license: MIT
compatibility: Target hosts need Linux with Python 3.8+ and systemd (cron works too). Needs SSH access with sudo from where the agent runs.
---

# Monitor Linux hosts with infrawatch

`scripts/infrawatch.py` runs once a minute from a systemd timer, reads the machine, compares with its last run, and
sends one message with what changed. No daemon, no database, no packages to install. Paths here are relative to
this skill's folder.

## What ends up on the host

| Path | What |
|---|---|
| `/opt/infrawatch/infrawatch.py` | the monitor (copy of `scripts/infrawatch.py`) |
| `/etc/infrawatch/config.ini` | what to watch and thresholds: no secrets, safe to read and show |
| `/etc/infrawatch/notify.env` | channel tokens, root only, mode 600: never print it |
| `/etc/systemd/system/infrawatch.{service,timer}` | runs it every minute as root |
| `/var/lib/infrawatch/state.json` | open alerts, baselines |

## Ground rules

- You are working on someone's servers. Reading is free; **say what you are about to change and get a yes** before
  installing, and never restart, stop or reconfigure anything that is not infrawatch.
- Never display `notify.env` or a token. `config.ini` holds none, so show it freely.
- One host at a time, verified, before the next. Do not fan out an untested config.
- If no channel exists yet (`~/.infra-alerts/notify.env` missing or empty), do `setup-alert-channel` first.

## Install on a host

Ask for the SSH target (an alias from `~/.ssh/config`, or `user@host`) and a short name for messages (`vps-01`).

1. **Look first** (read-only). Learn what is worth watching instead of asking the user to list it:

   ```bash
   ssh HOST 'hostname; python3 --version; systemctl --version | head -1; nproc; free -h | head -2; df -h -x tmpfs -x devtmpfs -x overlay -x squashfs
   systemctl list-units --type=service --state=running --no-legend --plain | awk "{print \$1}"
   docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}" 2>/dev/null
   ss -H -tln 2>/dev/null | awk "{print \$4}" | sort -u
   nvidia-smi -L 2>/dev/null'
   ```

   Python older than 3.8 or no systemd: see [Without systemd](#without-systemd) or stop and say so.

2. **Propose a config** and agree it with the user. Start from `assets/config.example.ini`. Pick only what would
   hurt if it stopped: the units and containers that serve users, the URLs of what they serve (probe them on
   `127.0.0.1` here; public URLs are better probed from outside, by `monitor-cloudflare`), database ports under
   `[tcp]`, certificates under `[certs]`. Leave the defaults for disks, memory and load. For a GPU machine read
   [references/gpu.md](references/gpu.md). Every check and threshold is in [references/checks.md](references/checks.md).

3. **Copy the files** (after the yes):

   ```bash
   scp scripts/infrawatch.py assets/infrawatch.service assets/infrawatch.timer CONFIG.ini HOST:/tmp/
   scp ~/.infra-alerts/notify.env HOST:/tmp/infrawatch-notify.env
   ssh HOST 'sudo install -d -m 755 /opt/infrawatch /etc/infrawatch &&
     sudo install -m 755 /tmp/infrawatch.py /opt/infrawatch/ &&
     sudo install -m 644 /tmp/CONFIG.ini /etc/infrawatch/config.ini &&
     sudo install -m 600 -o root -g root /tmp/infrawatch-notify.env /etc/infrawatch/notify.env &&
     sudo install -m 644 /tmp/infrawatch.service /tmp/infrawatch.timer /etc/systemd/system/ &&
     rm -f /tmp/infrawatch.py /tmp/CONFIG.ini /tmp/infrawatch-notify.env /tmp/infrawatch.service /tmp/infrawatch.timer'
   ```

4. **Dry run.** Nothing is sent or saved:

   ```bash
   ssh HOST 'sudo python3 /opt/infrawatch/infrawatch.py check'
   ```

   Read every line with the user. Fix what is wrong now, not after the first false alarm: a mount that should be
   ignored, a unit name that is `inactive` because it is spelled differently, a threshold already exceeded on a
   machine that is healthy by its own standards. A line `check:… could not run` is a bug in the check on this
   system: report it, and remove that check's section rather than ship a warning nobody can clear.

5. **Test the channel from the host** (a firewall can block what worked from the laptop):

   ```bash
   ssh HOST 'sudo python3 /opt/infrawatch/infrawatch.py test'
   ```

   Each channel prints `ok` or the service's own error. The user must see the message in the chat.

6. **Turn it on**, then confirm it ran:

   ```bash
   ssh HOST 'sudo systemctl daemon-reload && sudo systemctl enable --now infrawatch.timer &&
     sleep 70 && systemctl list-timers infrawatch.timer --no-pager && sudo journalctl -u infrawatch -n 5 --no-pager &&
     sudo python3 /opt/infrawatch/infrawatch.py status'
   ```

   If `daily_report` is set and its time has passed today, the first run sends the daily report: a second proof.

7. **Prove an alert arrives and clears** (offer it; takes about six minutes). Add a check that cannot pass, wait for
   the 🔴 message, remove it, wait for the ✅:

   ```bash
   ssh HOST "sudo sed -i '/^\[tcp\]/a selftest = 127.0.0.1:9' /etc/infrawatch/config.ini"     # nothing listens on port 9
   # 🔴 "tcp selftest" arrives after 3 runs. Then point it at a port that is open (sshd) so it recovers:
   ssh HOST "sudo sed -i 's/^selftest = .*/selftest = 127.0.0.1:22/' /etc/infrawatch/config.ini"
   # ✅ arrives after 2 runs. Then take it out:
   ssh HOST "sudo sed -i '/^selftest = /d' /etc/infrawatch/config.ini && sudo python3 /opt/infrawatch/infrawatch.py check"
   ```

   A check removed from the config while its alert is open is forgotten without a recovery message, which is why
   the test lets it recover first.

8. **Tell the user what is there**: the table above, the checks now active, how to silence and how to remove.

## Day to day

| To | Run on the host (with sudo) |
|---|---|
| See open alerts | `python3 /opt/infrawatch/infrawatch.py status` |
| Preview after a config change | `python3 /opt/infrawatch/infrawatch.py check` |
| Quiet during maintenance | `python3 /opt/infrawatch/infrawatch.py silence 2h` (`off` to end). It keeps watching; when the silence ends it says what is still wrong |
| Send the report now | `python3 /opt/infrawatch/infrawatch.py report` |
| See why nothing arrived | `journalctl -u infrawatch -n 50` (channel errors are logged there, without tokens) |
| Upgrade | copy the new `infrawatch.py` over the old one |
| Remove | `systemctl disable --now infrawatch.timer; rm -rf /opt/infrawatch /etc/infrawatch /var/lib/infrawatch /etc/systemd/system/infrawatch.{service,timer}; systemctl daemon-reload` |

Config changes take effect on the next run; nothing to restart.

## How alerts behave

- A problem is told after **3 bad readings in a row** (3 minutes) and cleared after **2 good ones**, so one bad
  minute tells nobody. A value on the line does not flap: an open alert clears only a little below its threshold.
- **One message per run** with everything that changed. New problems make a sound; recoveries and reports do not.
- A critical problem still open is said again every 4 hours; warnings are said once.
- Events (reboot, OOM kill, GPU Xid, security findings) are told once; there is nothing to recover from.
- A message no channel accepted is kept and sent with the next run.
- Every message carries a category: URGENT, ATTENTION, SECURITY, RESOLVED or STATS, as a word in the title
  (`[labels]`, any language: ask the user which words their team wants) and a fixed tag (`#urgent`, …). Each can
  go to its own Telegram topic. See [references/checks.md](references/checks.md#categories).

## Who watches the monitor

A host that is off sends nothing, and silence looks like health. Close the gap with one of:

- `monitor-cloudflare`: its Worker expects a heartbeat from each host after every run and alerts when one goes
  quiet. Set `HEARTBEAT_URL` and `HEARTBEAT_TOKEN` in `notify.env`.
- Two hosts watching each other: on each, an `[http]` check of something the other serves.
- Any dead man's switch service: put its ping URL in `HEARTBEAT_URL`.
- At the least, the daily report: if it did not come, look.

## Without systemd

Containers and some minimal systems have cron only. Same files, and instead of the units:

```
* * * * * root flock -n /run/infrawatch.lock python3 /opt/infrawatch/infrawatch.py run
```

in `/etc/cron.d/infrawatch`. Reboot, OOM, Xid and SSH-login events read the systemd journal and are skipped
without it; everything else works.
