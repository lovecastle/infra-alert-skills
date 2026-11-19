---
name: triage-alerts
description: Diagnoses an alert sent by infrawatch or the infra-alerts Cloudflare Worker, finds the cause on the server with read-only commands, proposes a fix, and tunes or silences alerts that are noise. Use when the user pastes or mentions an alert message that starts with "[infrawatch]" or "[infra-alerts]" (for example a full disk, a service that is not running, or a host with no heartbeat), asks why a server, GPU, container or site is down, says alerts are too noisy or missing, or wants to silence monitoring during maintenance. Triggers include "got an alert", "server is down", "disk full", "no heartbeat", "too many alerts", "bị báo lỗi", "server sập", "ổ đĩa đầy", "告警了", "服务器挂了", "磁盘满了".
license: MIT
---

# Triage an alert

An alert names a symptom. The job is to find the cause, say it plainly, and fix it with the user's yes.

## Rules

1. **Read before you touch.** Every command in the first column of the tables below only reads. Run those freely.
2. **Anything that changes the machine needs a yes first**: restarting a service, deleting files, killing a
   process, rebooting. Say what you found, what you propose, and what it will interrupt.
3. **Start from the alert's own words.** The line after the icon is the check and its reading: `disk /: 96% used`,
   `service nginx: inactive`, `http api: HTTP 502`. `[infrawatch]` came from a host; `[infra-alerts]` from the
   Cloudflare Worker.
4. **Do not make the alert go away; make the cause go away.** Raising a threshold or silencing is right only when
   the reading is normal for that machine.
5. **Finish by saying what happened** in two or three sentences the user could forward: cause, what was done,
   whether it can recur.

First look, on the host named in the alert:

```bash
ssh HOST 'sudo python3 /opt/infrawatch/infrawatch.py status; uptime; sudo journalctl -p err -n 30 --no-pager --since "-1h"'
```

## Alerts from a host (`[infrawatch]`)

| Alert line | Look at | Usual causes and fixes |
|---|---|---|
| `disk <mount>: N% used` | `df -h <mount>`; `sudo du -xh --max-depth=2 <mount> 2>/dev/null \| sort -h \| tail -20`; `docker system df`; `journalctl --disk-usage` | Logs (`journalctl --vacuum-size=500M`, logrotate), Docker images and build cache (`docker image prune`), old backups or dumps, a runaway temp directory. Deleting is the user's call: list the candidates with sizes |
| `inodes <mount>: N% used` | `sudo find <mount> -xdev -type f \| cut -d/ -f1-4 \| sort \| uniq -c \| sort -n \| tail` | Millions of small files: sessions, cache, mail queue |
| `memory: N% used` | `free -h`; `ps aux --sort=-%mem \| head -15`; `docker stats --no-stream` | A leak (restart that service, then find why), a job too big for the machine, or a threshold below this machine's normal |
| `out of memory, killed: X` | `journalctl -k --since "-1h" \| grep -i -B5 "killed process"`; the service's own log | The kernel killed X to survive. If X is a container with a memory limit, the limit is too low or the process leaks; if it is the whole machine, something else grew |
| `load: N per core` | `uptime`; `top -bn1 \| head -20`; `iostat -x 1 3` (if installed); `ps aux --sort=-%cpu \| head` | A hot process, disk I/O wait (high `wa`), a traffic spike, a crawler |
| `service X: inactive/failed`, `failed units: …` | `systemctl status X --no-pager`; `journalctl -u X -n 80 --no-pager` | The log's last lines say why it exited. Fix that first; `systemctl restart X` after. `systemctl reset-failed X` clears a unit that failed once and is no longer needed |
| `container X: exited/missing`, `containers restarting or unhealthy` | `docker ps -a --filter name=X`; `docker logs --tail 80 X`; `docker inspect X --format "{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}"` | Exit 137 with OOMKilled true: memory limit. A crash loop: read the log. Missing: someone removed or renamed it; update `[services] containers` if on purpose |
| `http X: HTTP 5xx / no answer` | `curl -sv -m 10 URL`; the service behind it (rows above); the proxy's log (nginx, caddy) | 502/504: the app behind the proxy is down or slow. Timeout: the app hangs, or a firewall. Refused: nothing listens |
| `tcp X: …` | `ss -tlnp \| grep PORT`; the service's status and log | The service is down, or listens on another address |
| `certificate X: N days left / invalid` | `echo \| openssl s_client -connect X:443 -servername X 2>/dev/null \| openssl x509 -noout -dates -issuer`; `systemctl list-timers \| grep -i -E "certbot\|acme"`; the renewer's log | Renewal is failing: DNS changed, port 80 closed, a stopped timer. Renew by hand, then fix the renewer |
| `host rebooted` | `last -x reboot shutdown \| head -5`; `journalctl -b -1 -n 50 --no-pager` (the end of the previous boot) | A planned reboot, the provider's maintenance, a kernel panic or power loss (the previous boot's log ends abruptly), unattended upgrades |
| `nvidia-smi: not answering`, `N of M GPUs visible` | `nvidia-smi`; `sudo dmesg \| grep -i -E "nvrm\|xid" \| tail -20`; `lsmod \| grep nvidia` | A kernel update without the matching driver module (reinstall or DKMS rebuild, reboot), or the card fell off the bus (Xid 79: reboot; if it returns, hardware) |
| `GPU Xid N` | `sudo dmesg \| grep -i xid \| tail`; the table in monitor-hosts `references/gpu.md` | 13/31/43: the application crashed, restart the job. 48/79 and the others marked: reset or reboot; repeated: hardware |
| `GPU N: T°C` | `nvidia-smi --query-gpu=temperature.gpu,fan.speed,power.draw,utilization.gpu --format=csv -l 2` | Blocked airflow, a dead fan, dust, a hot room. Lower the load now (`nvidia-smi -pl WATTS` caps power), fix the cooling after |
| `GPU N memory: N%` | `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` | A leaked or zombie process holding memory, two jobs on one card |
| `GPU idle for …`, `GPU process X: not on the GPU` | the job's service or container status and log; `nvidia-smi` | The job crashed, finished, or restarted on CPU. If idle is normal for this machine, set `idle_minutes = 0` |
| `new file / file changed / new public listening port / new container / SSH login …` | Ask first: "was that you or a deploy?" If not: `sudo last -a \| head`; `sudo ss -tlnp`; `ls -la --time-style=full-iso FILE`; `sudo journalctl -u ssh -u sshd --since "-2h" --no-pager \| grep Accepted` | Expected after a deploy or a new key. Unexplained: treat as an incident. Do not clean up; tell the user what you see and let them decide (rotate keys, isolate the host) |
| `N failed SSH logins` | `sudo journalctl -u ssh -u sshd --since "-10min" --no-pager \| grep -c -E "Failed\|Invalid"` | Internet background noise on a server with password login open. Turn password login off, or raise `ssh_fails_per_min` |
| `check_… could not run` | `sudo python3 /opt/infrawatch/infrawatch.py check` | A check does not work on this system: report the message; remove that section from the config meanwhile |

## Alerts from the Worker (`[infra-alerts]`)

| Alert line | Look at | Usual causes |
|---|---|---|
| `<host>: no heartbeat for …` | Can you SSH to it? Yes: `systemctl status infrawatch.timer`; `sudo journalctl -u infrawatch -n 30 --no-pager`. No: the provider's console and status page | The host is down or off the network; or only the monitor stopped (timer disabled, a config that cannot be read, a changed token: the journal says `heartbeat not taken`) |
| `<probe>: HTTP 5xx` | `curl -sv -m 10 URL` from your own machine; the origin's service (host table) | 502/503/504 or Cloudflare's 520–526: the origin is down, slow or refusing Cloudflare. 521: nothing listens on the origin. 522/524: timeout. 525/526: the origin's certificate |
| `<probe>: HTTP 403 / 429` | Cloudflare dashboard → Security → Events, filter User-Agent `infra-alerts-probe` | A WAF, bot or rate rule is stopping the probe, not the site. Exempt the probe or probe a health path |
| `<probe>: … lacks "text"` | `curl -s URL \| head -40` | The page answers but with something else: a maintenance page, an error rendered with status 200, a login wall |
| `<probe>: no answer (timed out)` | The same from your machine and from the host itself | Origin overloaded, or DNS |

## When alerts are the problem

| Complaint | Do |
|---|---|
| It alerts, then recovers, again and again | Something really is flapping: find it. If it is a value near its threshold, move the threshold to match the machine (`references/checks.md` in monitor-hosts) |
| A warning that is always there | Normal for this machine: raise that threshold, or drop the check. A permanent warning hides the next real one |
| Too many messages during a deploy or maintenance | `sudo python3 /opt/infrawatch/infrawatch.py silence 1h` before; it reports what is still wrong when the silence ends |
| The reminder every 4 hours is annoying | It is saying something is still broken. Fix it, or set `remind_hours = 0` |
| Nothing arrived but something was down | `journalctl -u infrawatch -n 50`: was it running, did the channel refuse? Was the outage shorter than 3 minutes (`fail_threshold`)? Is that thing in the config at all? Add the missing check |
| No daily report today | The monitor or the channel is down: treat it as an alert |

After any incident ask one question: what check would have told us earlier? Add it.
