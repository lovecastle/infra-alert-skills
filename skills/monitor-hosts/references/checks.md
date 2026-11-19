# infrawatch checks and settings

Contents: [Readings](#readings) · [Events](#events) · [Security baseline](#security-baseline) ·
[Choosing thresholds](#choosing-thresholds) · [Message format](#message-format) · [Where secrets live](#where-secrets-live)

Every setting is in `assets/config.example.ini` with its default. This file says what each check reads and when it
alerts. The key in the first column is what `infrawatch.py check` and `status` print.

## Readings

A reading has a level (ok, warning 🟠, critical 🔴) every run. It alerts after `fail_threshold` bad runs in a row
(default 3) and recovers after `ok_threshold` good ones (default 2).

| Key | Reads | Warning | Critical | Config |
|---|---|---|---|---|
| `disk:<mount>` | Used space of each real filesystem (`/proc/mounts`; tmpfs, overlay, snap loops left out; a disk mounted in several places counted once) | ≥ 85 % | ≥ 95 % | `[thresholds] disk_warn/disk_crit`, `[disks] ignore` |
| `inode:<mount>` | Used inodes (many small files fill a disk that still shows free space) | ≥ 90 % | ≥ 98 % | `inode_warn/inode_crit` |
| `memory` | 100 − MemAvailable/MemTotal: what the kernel says is really unavailable, cache excluded | ≥ 90 % | ≥ 97 % | `mem_warn/mem_crit` |
| `swap` | Used swap | ≥ 80 % | – | `swap_warn` |
| `load` | 5-minute load average ÷ cores | ≥ 1.5 | ≥ 3 | `load_warn/load_crit` |
| `unit:<name>` | `systemctl is-active` for each unit listed | – | not `active` | `[services] units` |
| `units:failed` | Any unit in the failed state | any | – | `[services] failed_units` |
| `docker` | Docker answers (only when containers are listed) | – | no | `[services] containers` |
| `container:<name>` | Each listed container is `running` | – | exited, missing, … | `[services] containers` |
| `containers:unhealthy` | Any container restarting or with a failing healthcheck | any | – | – |
| `http:<name>` | GET the URL: 2xx/3xx, and the body contains the text if one is given | – | else | `[http] name = URL \| text` |
| `tcp:<name>` | The port accepts a connection within 5 s | – | else | `[tcp] name = host:port` |
| `cert:<host>` | Days left on the TLS certificate (asked every 6 h); an expired or mismatched one fails the handshake | ≤ 14 | ≤ 3, or invalid | `[certs] hosts`, `cert_days_warn/crit` |
| `gpu:smi` | `nvidia-smi` answers | – | no | `[gpu] enabled` |
| `gpu:count` | As many GPUs as were ever seen | – | fewer | – |
| `gpu<N>:temp` | Temperature | ≥ 85 °C | ≥ 92 °C | `gpu_temp_warn/crit` |
| `gpu<N>:mem` | Memory used | ≥ 90 % | ≥ 98 % | `gpu_mem_warn/crit` |
| `gpu:idle` | Every GPU under `idle_util` % for `idle_minutes` | yes | – | `[gpu] idle_minutes`, `idle_util` |
| `gpu:proc:<name>` | A process with this name is on a GPU | – | no | `[gpu] processes` |
| `check:<name>` | A check itself raised an error on this system | yes | – | fix or remove that section |

An open alert clears a little below its threshold (3 points for disks and GPU, 5 for memory, 0.2 for load), so a
value sitting on the line does not open and close all day.

## Events

Told once, in the run that sees them. They need the systemd journal.

| Event | Source |
|---|---|
| Host rebooted, and how long it has been up | the kernel's boot id changed since the last run |
| Out of memory: which processes were killed, how many times | kernel log, `Out of memory: Killed process …` (system-wide and cgroup/container) |
| GPU Xid error, with a note when the code usually needs a reset or reboot | kernel log, `NVRM: Xid` |

## Security baseline

Off by default (`[security] enabled = true` to turn on). It is a tripwire, not an intrusion detection system: it
tells you about a change so a human can say "that was me" or not. It never kills, blocks or reverts anything.

The first run learns what exists and says so once ("security baseline learned: …"). After that, told once each:

- a watched file is new, changed or removed: `/etc/passwd`, `/etc/shadow`, `/etc/sudoers` and `sudoers.d`, every
  `authorized_keys`, `sshd_config` and its drop-ins, `/etc/crontab`, `cron.d`, user crontabs, systemd unit and timer
  files in `/etc/systemd/system`, plus `extra_files`
- a listening port open to other machines that was never seen before (loopback is ignored)
- a container name never seen before
- an SSH login by a user+address pair or a key fingerprint never seen before; a password login every time
- failed SSH logins above `ssh_fails_per_min` (default 20 a minute, averaged over the time since the last run)

Expected noise: deploying a service, adding a key, opening a port will each ping once. That is the point; nothing
to do when it was you. A server with password SSH open to the internet sees constant failed logins: raise
`ssh_fails_per_min`, or better, turn password login off.

To learn again from scratch, remove `/var/lib/infrawatch/state.json` (open alerts are forgotten too, and open
again if still true).

## Choosing thresholds

- Defaults are deliberately not tight. An alert that fires on a healthy machine teaches people to ignore the chat.
- Set a threshold from what the machine normally does, not from a rule of thumb: a database host at 92 % memory
  every day needs `mem_warn = 95`, not a daily warning.
- Disks: what matters is time to full. A 2 TB data disk at 85 % has 300 GB left; a 20 GB root disk at 85 % has 3.
  Lower the root threshold or raise the data one accordingly (thresholds are global; use `[disks] ignore` for a
  mount you watch another way).
- Load: 1.5 per core sustained for 5 minutes means requests are queueing. Batch machines that run at 100 % by
  design want `load_warn = 4` or so.
- `fail_threshold = 3` means an outage shorter than three minutes is never told. For something that must not blink,
  probe it from outside as well (`monitor-cloudflare`, 2 minutes by default).

## Message format

```
[infrawatch] 🔴 vps-01: 1 problem, 1 resolved
🔴 disk /: 96% used, 1.8 GB free
✅ http api: HTTP 200 (after 12m)
```

One message per run. The title counts new problems, reminders ("still open"), recoveries and events. `[notify]
prefix` changes `[infrawatch]`; keep the word `infra` in it if a DingTalk or Feishu bot uses keyword security.

## Where secrets live

`/etc/infrawatch/notify.env`, root only, mode 600, `KEY=VALUE` lines: the `[notify]` keys in capitals
(`TG_BOT_TOKEN`, `TG_CHAT_ID`, `QYWX_KEY`, …) and `HEARTBEAT_URL`, `HEARTBEAT_TOKEN`. The environment overrides
it; it overrides `config.ini`. infrawatch warns in the journal when a file holding tokens is readable by others.
