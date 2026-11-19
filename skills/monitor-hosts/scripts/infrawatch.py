#!/usr/bin/env python3
"""infrawatch: a one-file host monitor that tells a chat when something breaks and when it is back.

Run once a minute by a systemd timer (or cron). Each run reads the machine (disks, memory, load, services, containers,
HTTP/TCP endpoints, certificates, NVIDIA GPUs, optional security baseline), compares with the last run kept in a JSON
state file, and sends ONE message with what changed: new problems, problems that got worse, recoveries, one-shot events.
No daemon, no database, no dependencies: Python 3.8+ standard library only.

    infrawatch.py check      read everything and print it; nothing is sent or saved (use this first)
    infrawatch.py test       send a test message to every configured channel
    infrawatch.py chats      list the Telegram chats the bot can see, to find a chat id
    infrawatch.py run        one monitoring run (what the timer calls)
    infrawatch.py status     the alerts open right now
    infrawatch.py report     send the daily report now
    infrawatch.py silence 2h | off     keep watching but send nothing for a while
"""
import argparse
import base64
import collections
import configparser
import fnmatch
import glob
import hashlib
import hmac
import html
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.2.0"
OK, WARN, CRIT = "ok", "warn", "crit"
RANK = {OK: 0, WARN: 1, CRIT: 2}
ICON = {OK: "✅", WARN: "🟠", CRIT: "🔴"}
# What a message asks of whoever reads it. Each message carries one category: its word in the title ([labels], any
# language) and a tag on its last line, the same everywhere, so a chat can be filtered by it. On Telegram each
# category can also go to its own topic of a forum group (tg_thread_<category>).
CATS = {"urgent": "🔴", "attention": "🟠", "security": "🛡", "resolved": "✅", "stats": "📊"}

# A reading of one check (instant: opens and closes on a single reading, for checks that debounce themselves) and a
# one-shot event (a reboot, an OOM kill, a new SSH login: told once, nothing to recover from).
R = collections.namedtuple("R", "key level text instant", defaults=(False,))
E = collections.namedtuple("E", "key text")

DEFAULTS = {
    "host": {"name": "", "daily_report": "08:00"},
    "notify": {"prefix": "[infrawatch]", "tg_bot_token": "", "tg_chat_id": "", "tg_thread_id": "", "tg_api_host": "",
               "qywx_key": "", "fskey": "", "fssecret": "", "fs_host": "", "dd_bot_token": "", "dd_bot_secret": "",
               "bark_push": "", "ntfy_url": "", "ntfy_topic": "", "ntfy_token": "", "push_key": "",
               "push_plus_token": "", "webhook_url": "", **{"tg_thread_" + c: "" for c in CATS}},
    "labels": {"urgent": "URGENT", "attention": "ATTENTION", "security": "SECURITY", "resolved": "RESOLVED", "stats": "STATS"},
    "heartbeat": {"url": "", "token": ""},
    "alerting": {"fail_threshold": "3", "ok_threshold": "2", "remind_hours": "4"},
    "thresholds": {"disk_warn": "85", "disk_crit": "95", "inode_warn": "90", "inode_crit": "98", "mem_warn": "90",
                   "mem_crit": "97", "swap_warn": "80", "load_warn": "1.5", "load_crit": "3", "gpu_temp_warn": "85",
                   "gpu_temp_crit": "92", "gpu_mem_warn": "90", "gpu_mem_crit": "98", "cert_days_warn": "14",
                   "cert_days_crit": "3"},
    "disks": {"ignore": "/boot/efi, /snap/*, /run/*, /var/lib/docker/*"},
    "services": {"units": "", "containers": "", "failed_units": "true"},
    "http": {}, "tcp": {},
    "certs": {"hosts": ""},
    "gpu": {"enabled": "auto", "idle_minutes": "0", "idle_util": "5", "processes": ""},
    "security": {"enabled": "false", "extra_files": "", "ssh_fails_per_min": "20"},
}


# ---------- small helpers (tests replace sh / read / http / statvfs / loadavg) ----------
def sh(*cmd, timeout=20):
    """Run a command → (exit code, stdout); 127 when it is not installed, 124 when it took too long."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, universal_newlines=True)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


statvfs = getattr(os, "statvfs", None)
loadavg = getattr(os, "getloadavg", None)


def http(url, data=None, headers=None, timeout=10, form=False):
    """GET, or POST when data is given (a dict → JSON, or form fields) → (status, body). Status 0 = no answer, the
    body then says why. Never raises, and never puts the URL (it may hold a token) in what it returns."""
    h = {"User-Agent": "infrawatch/" + VERSION}
    h.update(headers or {})
    body = None
    if data is not None:
        if form:
            body, h["Content-Type"] = urllib.parse.urlencode(data).encode(), "application/x-www-form-urlencoded"
        else:
            body, h["Content-Type"] = json.dumps(data).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=timeout) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536).decode("utf-8", "replace")
    except Exception as e:  # DNS, refused, timeout, TLS
        return 0, str(getattr(e, "reason", e))


def csv(s):
    return [x.strip() for x in s.replace("\n", ",").split(",") if x.strip()]


def dur(s):
    s = int(s)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return "%dd %dh" % (d, h) if d else "%dh %dm" % (h, m) if h else "%dm" % m if m else "%ds" % s


def size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.0f %s" % (n, unit) if unit in ("B", "KB", "MB") else "%.1f %s" % (n, unit)
        n /= 1024.0


def was(st, key):
    """The level this check was last announced at: what the chat believes."""
    return st.get("checks", {}).get(key, {}).get("level", OK)


def grade(value, warn, crit, before, margin):
    """A number against its thresholds. An alert already open only clears `margin` below its threshold, so a value
    sitting on the line does not flap."""
    if value >= crit or (before == CRIT and value >= crit - margin):
        return CRIT
    if value >= warn or (before != OK and value >= warn - margin):
        return WARN
    return OK


# ---------- checks: each takes (cfg, st, now) and returns readings and events ----------
def mounts(text):
    """/proc/mounts → {device: mountpoint} of real filesystems, the shortest mountpoint of each device (a disk
    bind-mounted into containers is one disk)."""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        dev, mnt, fs = p[0], p[1].replace("\\040", " "), p[2]
        real = (dev.startswith("/dev/") and not dev.startswith("/dev/loop")) or fs in ("zfs", "nfs", "nfs4", "cifs")
        if real and (dev not in out or len(mnt) < len(out[dev])):
            out[dev] = mnt
    return out


def check_disks(cfg, st, now):
    t, res = cfg["thresholds"], []
    ignore = csv(cfg["disks"]["ignore"])
    for mnt in sorted(set(mounts(read("/proc/mounts") or "").values())):
        if any(fnmatch.fnmatch(mnt, g) for g in ignore):
            continue
        try:
            v = statvfs(mnt)
        except OSError:
            continue
        used = v.f_blocks - v.f_bfree
        if used + v.f_bavail <= 0:
            continue
        pct = 100.0 * used / (used + v.f_bavail)  # as df counts it: of what a normal user can fill
        res.append(R("disk:" + mnt, grade(pct, t.getfloat("disk_warn"), t.getfloat("disk_crit"), was(st, "disk:" + mnt), 3),
                     "disk %s: %.0f%% used, %s free" % (mnt, pct, size(v.f_bavail * v.f_frsize))))
        if v.f_files:
            ipct = 100.0 * (v.f_files - v.f_ffree) / v.f_files
            res.append(R("inode:" + mnt, grade(ipct, t.getfloat("inode_warn"), t.getfloat("inode_crit"), was(st, "inode:" + mnt), 3),
                         "inodes %s: %.0f%% used" % (mnt, ipct)))
    return res


def meminfo(text):
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.split():
            out[k] = int(v.split()[0])  # kB
    return out


def check_memory(cfg, st, now):
    m, t, res = meminfo(read("/proc/meminfo") or ""), cfg["thresholds"], []
    if m.get("MemTotal") and "MemAvailable" in m:
        used = 100.0 * (1 - m["MemAvailable"] / m["MemTotal"])
        res.append(R("memory", grade(used, t.getfloat("mem_warn"), t.getfloat("mem_crit"), was(st, "memory"), 5),
                     "memory: %.0f%% used, %s available" % (used, size(m["MemAvailable"] * 1024))))
    if m.get("SwapTotal"):
        swap = 100.0 * (1 - m.get("SwapFree", 0) / m["SwapTotal"])
        res.append(R("swap", grade(swap, t.getfloat("swap_warn"), 101, was(st, "swap"), 10), "swap: %.0f%% used" % swap))
    return res


def check_load(cfg, st, now):
    if not loadavg:
        return []
    t, per = cfg["thresholds"], loadavg()[1] / (os.cpu_count() or 1)  # the 5-minute average: a spike is not an alert
    return [R("load", grade(per, t.getfloat("load_warn"), t.getfloat("load_crit"), was(st, "load"), 0.2),
              "load: %.2f per core (5 min)" % per)]


def since_last(st, now):
    """Where the logs were read up to; an agent that was off for days does not replay them."""
    return int(max(st.get("last_run", now - 60), now - 3600))


XID_RESET = {48, 62, 64, 74, 79, 94, 95, 109, 119, 120}  # usually need a GPU reset or a reboot (NVIDIA's Xid catalog)


def check_kernel(cfg, st, now):
    """One-shot events: the machine rebooted, the kernel killed a process for memory, an NVIDIA Xid error."""
    ev = []
    boot = (read("/proc/sys/kernel/random/boot_id") or "").strip()
    if boot:
        if st.get("boot") and st["boot"] != boot:
            up = float((read("/proc/uptime") or "0").split()[0])
            ev.append(E("reboot", "host rebooted, up %s" % dur(up)))
        st["boot"] = boot
    code, out = sh("journalctl", "-k", "--no-pager", "-o", "cat", "--since", "@%d" % since_last(st, now))
    if code == 0:
        oom = re.findall(r"[Oo]ut of memory: Killed process (\d+) \(([^)]+)\)", out)
        if oom:
            names = collections.Counter(n for _, n in oom)
            ev.append(E("oom", "out of memory, killed: " + ", ".join("%s%s" % (n, " ×%d" % c if c > 1 else "") for n, c in names.most_common(5))))
        for bus, code_ in sorted(set(re.findall(r"NVRM: Xid \(([^)]*)\): (\d+)", out))):
            ev.append(E("xid", "GPU Xid %s on %s%s" % (code_, bus, " (usually needs a GPU reset or reboot)" if int(code_) in XID_RESET else "")))
    return ev


def check_units(cfg, st, now):
    res, units = [], csv(cfg["services"]["units"])
    if units:
        states = sh("systemctl", "is-active", *units)[1].split()
        for i, u in enumerate(units):
            s = states[i] if i < len(states) else "unknown"
            res.append(R("unit:" + u, OK if s == "active" else CRIT, "service %s: %s" % (u, s)))
    if cfg.getboolean("services", "failed_units"):
        code, out = sh("systemctl", "list-units", "--state=failed", "--no-legend", "--plain")
        if code == 0:
            failed = [line.split()[0] for line in out.splitlines() if line.strip()]
            res.append(R("units:failed", WARN if failed else OK, "failed units: " + (", ".join(failed[:8]) or "none")))
    return res


def check_containers(cfg, st, now):
    want = csv(cfg["services"]["containers"])
    code, out = sh("docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}")
    if code != 0:
        return [R("docker", CRIT, "docker: not answering")] if want else []  # no Docker and none asked for: nothing to say
    rows = dict((p[0], (p[1], p[2])) for p in (line.split("\t") for line in out.splitlines()) if len(p) == 3)
    res = [R("docker", OK, "docker: answering")] if want else []
    for n in want:
        s = rows.get(n, ("missing", ""))[0]
        res.append(R("container:" + n, OK if s == "running" else CRIT, "container %s: %s" % (n, s)))
    bad = sorted(n for n, (s, status) in rows.items() if s == "restarting" or "unhealthy" in status)
    if rows:
        res.append(R("containers:unhealthy", WARN if bad else OK, "containers restarting or unhealthy: " + (", ".join(bad[:8]) or "none")))
    return res


def check_http(cfg, st, now):
    """[http] name = URL            any 2xx/3xx answer is fine
              name = URL | text     ...and the body must contain the text"""
    res = []
    for name, spec in cfg["http"].items():
        url, _, want = (x.strip() for x in spec.partition(" | "))
        code, body = http(url)
        if not code:
            level, said = CRIT, "no answer (%s)" % body[:80]
        elif not 200 <= code < 400:
            level, said = CRIT, "HTTP %d" % code
        elif want and want not in body:
            level, said = CRIT, "HTTP %d but the answer lacks %r" % (code, want)
        else:
            level, said = OK, "HTTP %d" % code
        res.append(R("http:" + name, level, "http %s: %s" % (name, said)))
    return res


def check_tcp(cfg, st, now):
    """[tcp] name = host:port"""
    res = []
    for name, target in cfg["tcp"].items():
        host, _, port = target.strip().rpartition(":")
        try:
            socket.create_connection((host, int(port)), timeout=5).close()
            res.append(R("tcp:" + name, OK, "tcp %s: open" % name))
        except (OSError, ValueError) as e:
            res.append(R("tcp:" + name, CRIT, "tcp %s: %s" % (name, str(e)[:80])))
    return res


def cert_days(target):
    host, _, port = target.partition(":")
    with socket.create_connection((host, int(port or 443)), timeout=10) as s:
        with ssl.create_default_context().wrap_socket(s, server_hostname=host) as t:
            return (ssl.cert_time_to_seconds(t.getpeercert()["notAfter"]) - time.time()) / 86400


def check_certs(cfg, st, now):
    """Days left on each host's certificate, asked every 6 hours (a failed ask again after 5 minutes). An expired or
    mismatched certificate fails the handshake and is critical."""
    res, kept, t = [], st.setdefault("certs", {}), cfg["thresholds"]
    for h in csv(cfg["certs"]["hosts"]):
        at, days, why = kept.get(h, (0, None, None))
        if now - at > (300 if why else 6 * 3600):
            try:
                days, why = cert_days(h), None
            except Exception as e:
                days, why = None, str(e)[:120]
            kept[h] = [now, days, why]
        if why:
            res.append(R("cert:" + h, CRIT, "certificate %s: %s" % (h, why)))
        else:
            level = CRIT if days <= t.getfloat("cert_days_crit") else WARN if days <= t.getfloat("cert_days_warn") else OK
            res.append(R("cert:" + h, level, "certificate %s: %d days left" % (h, days)))
    return res


def gpus(text):
    """nvidia-smi --query-gpu=index,temperature.gpu,utilization.gpu,memory.used,memory.total,name (csv, no header, no
    units) → one dict a card; a value the card does not report ("[N/A]") is None."""
    def num(x):
        return float(x) if re.match(r"^-?\d+(\.\d+)?$", x) else None
    out = []
    for line in text.strip().splitlines():
        p = [x.strip() for x in line.split(",", 5)]
        if len(p) == 6:
            out.append({"i": p[0], "temp": num(p[1]), "util": num(p[2]), "used": num(p[3]), "total": num(p[4]), "name": p[5]})
    return out


def check_gpu(cfg, st, now):
    g, t = cfg["gpu"], cfg["thresholds"]
    mode = g["enabled"].strip().lower()
    if mode in ("false", "no", "off", "0"):
        return []
    code, out = sh("nvidia-smi", "--query-gpu=index,temperature.gpu,utilization.gpu,memory.used,memory.total,name", "--format=csv,noheader,nounits")
    seen = st.get("gpus", 0)
    if code == 127 and mode == "auto" and not seen:
        return []  # no NVIDIA driver here and none ever seen
    cards = gpus(out) if code == 0 else []
    if not cards:
        return [R("gpu:smi", CRIT, "nvidia-smi: not answering (exit %d)" % code)]
    st["gpus"] = max(seen, len(cards))
    res = [R("gpu:smi", OK, "nvidia-smi: answering"),
           R("gpu:count", CRIT if len(cards) < st["gpus"] else OK, "%d of %d GPUs visible" % (len(cards), st["gpus"]))]
    for c in cards:
        k = "gpu%s" % c["i"]
        if c["temp"] is not None:
            res.append(R(k + ":temp", grade(c["temp"], t.getfloat("gpu_temp_warn"), t.getfloat("gpu_temp_crit"), was(st, k + ":temp"), 3),
                         "GPU %s (%s): %.0f°C" % (c["i"], c["name"], c["temp"])))
        if c["used"] is not None and c["total"]:
            pct = 100.0 * c["used"] / c["total"]
            res.append(R(k + ":mem", grade(pct, t.getfloat("gpu_mem_warn"), t.getfloat("gpu_mem_crit"), was(st, k + ":mem"), 3),
                         "GPU %s memory: %.0f%% (%.1f of %.1f GB)" % (c["i"], pct, c["used"] / 1024, c["total"] / 1024)))
    if g.getfloat("idle_minutes"):  # a job that died leaves the card idle: told once it has been idle this long
        use = [c["util"] for c in cards if c["util"] is not None]
        if use and max(use) < g.getfloat("idle_util"):
            st.setdefault("gpu_idle_since", now)
        else:
            st.pop("gpu_idle_since", None)
        idle = "gpu_idle_since" in st and now - st["gpu_idle_since"] >= g.getfloat("idle_minutes") * 60
        res.append(R("gpu:idle", WARN if idle else OK, "GPU idle for %s (under %g%% use)" % (dur(now - st["gpu_idle_since"]), g.getfloat("idle_util")) if idle else "GPU: in use", True))
    names = csv(g["processes"])
    if names:
        apps = sh("nvidia-smi", "--query-compute-apps=process_name", "--format=csv,noheader")[1]
        for n in names:
            res.append(R("gpu:proc:" + n, OK if n in apps else CRIT, "GPU process %s: %s" % (n, "running" if n in apps else "not on the GPU")))
    return res


SEC_FILES = ["/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/sudoers.d/*", "/root/.ssh/authorized_keys",
             "/home/*/.ssh/authorized_keys", "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*", "/etc/crontab",
             "/etc/cron.d/*", "/var/spool/cron/crontabs/*", "/etc/systemd/system/*.service", "/etc/systemd/system/*.timer"]
ACCEPTED = re.compile(r"Accepted (\S+) for (\S+) from (\S+)(?:.*?(SHA256:\S+))?")
FAILED = re.compile(r"Failed password|Invalid user|authentication failure")


def file_hashes(globs):
    out = {}
    for g in globs:
        for p in glob.glob(g):
            try:
                with open(p, "rb") as f:
                    out[p] = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                pass  # a directory, or not ours to read
    return out


def listening(text):
    """`ss -H -tln` → the sockets open to other machines ("0.0.0.0:22", "[::]:443"); loopback is left out."""
    out = set()
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 4 and not p[3].startswith("127.") and not p[3].startswith("[::1]"):
            out.add(p[3])
    return out


def check_security(cfg, st, now):
    """Off unless [security] enabled = true. The first run learns what is normal; after that, each thing never seen
    before is told once: an auth or persistence file new/changed/removed, a new public listening port, a new
    container, an SSH login by a user+address or key never seen (a password login always), a burst of failed logins.
    It only tells; it never kills or blocks anything."""
    s = cfg["security"]
    if not s.getboolean("enabled"):
        return []
    sec, first = st.get("sec"), "sec" not in st
    if first:
        sec = st["sec"] = {"files": {}, "ports": [], "containers": [], "logins": [], "keys": []}
    ev = []
    files = file_hashes(SEC_FILES + csv(s["extra_files"]))
    for p in sorted(set(files) | set(sec["files"])):
        a, b = sec["files"].get(p), files.get(p)
        if a != b:
            ev.append(E("sec:file", "%s: %s" % ("new file" if a is None else "file removed" if b is None else "file changed", p)))
    sec["files"] = files

    def unseen(kind, found, label):
        for x in sorted(set(found) - set(sec[kind])):
            ev.append(E("sec:" + kind, label + x))
        sec[kind] = sorted(set(sec[kind]) | set(found))  # kept for good: a service restarting is not a new port

    code, out = sh("ss", "-H", "-tln")
    if code == 0:
        unseen("ports", listening(out), "new public listening port: ")
    code, out = sh("docker", "ps", "-a", "--format", "{{.Names}}")
    if code == 0:
        unseen("containers", out.split(), "new container: ")
    since = since_last(st, now)
    code, out = sh("journalctl", "-u", "ssh", "-u", "sshd", "--no-pager", "-o", "cat", "--since", "@%d" % since)
    if code == 0:
        fails = 0
        for line in out.splitlines():
            m = ACCEPTED.search(line)
            if m:
                how, user, ip, key = m.groups()
                who = "%s@%s" % (user, ip)
                if who not in sec["logins"] or (key and key not in sec["keys"]) or how == "password":
                    ev.append(E("sec:login", "SSH login: %s from %s (%s%s)" % (user, ip, how, ", new key" if key and key not in sec["keys"] else "")))
                sec["logins"] = sorted(set(sec["logins"]) | {who})
                if key:
                    sec["keys"] = sorted(set(sec["keys"]) | {key})
            elif FAILED.search(line):
                fails += 1
        if fails >= s.getfloat("ssh_fails_per_min") * max(1.0, (now - since) / 60.0):
            ev.append(E("sec:fails", "%d failed SSH logins in %s" % (fails, dur(max(60, now - since)))))
    if first:
        return [E("sec:baseline", "security baseline learned: %d files, %d public ports, %d containers" % (len(files), len(sec["ports"]), len(sec["containers"])))]
    return ev


CHECKS = [check_disks, check_memory, check_load, check_kernel, check_units, check_containers, check_http, check_tcp,
          check_certs, check_gpu, check_security]


# ---------- the alert state machine ----------
def evaluate(st, readings, now, fail_n=3, ok_n=2, remind_s=4 * 3600):
    """Readings → notices [(kind, level, key, text)], kind open / worse / remind / resolved. A check alerts after
    fail_n bad readings in a row and recovers after ok_n good ones, so one bad minute tells nobody. Only changes are
    notices; a critical alert still open is told again every remind_s (0 = never). st["checks"] is updated in place."""
    checks, notices = st.setdefault("checks", {}), []
    for r in readings:
        c = checks.setdefault(r.key, {"level": OK, "bad": 0, "good": 0})
        need_bad, need_good = (1, 1) if r.instant else (fail_n, ok_n)
        if r.level != OK:
            c["good"] = 0
            if c["level"] == OK:
                c["bad"] += 1
                if c["bad"] >= need_bad:
                    c.update(level=r.level, since=now, told=now)
                    notices.append(("open", r.level, r.key, r.text))
            elif RANK[r.level] > RANK[c["level"]]:
                c.update(level=r.level, told=now)
                notices.append(("worse", r.level, r.key, r.text))
            else:
                c["level"] = r.level  # critical → warning: quieter, said only when it recovers
                if r.level == CRIT and remind_s and now - c.get("told", now) >= remind_s:
                    c["told"] = now
                    notices.append(("remind", r.level, r.key, "%s (for %s)" % (r.text, dur(now - c["since"]))))
            c["text"] = r.text
        elif c["level"] != OK:
            c["good"] += 1
            if c["good"] >= need_good:
                notices.append(("resolved", OK, r.key, "%s (after %s)" % (r.text, dur(now - c["since"]))))
                checks[r.key] = {"level": OK, "bad": 0, "good": 0}
        else:
            c["bad"] = 0
    seen = set(r.key for r in readings)
    for k in [k for k in checks if k not in seen]:  # a check taken out of the config, a disk unmounted
        del checks[k]
    return notices


def headline(prefix, cat, host, said, labels=None):
    return "%s %s %s | %s: %s" % (prefix, CATS[cat], (labels or DEFAULTS["labels"])[cat], host, said)


def tags(cat, host):
    return "#%s #%s" % (cat, re.sub(r"\W", "_", host))


def compose(prefix, host, notices, events, labels=None):
    """One run's changes → (title, lines, loud, category). loud: something new is wrong (the rest arrives without
    sound). The category is the most pressing thing in the message: urgent (something critical is open), attention
    (a warning, or an event such as a reboot), security (only security events), resolved (only recoveries)."""
    lines = ["%s %s%s" % (ICON[level], "still: " if kind == "remind" else "", text) for kind, level, _, text in notices]
    lines += ["⚡ " + e.text for e in events]
    count = collections.Counter(kind for kind, _, _, _ in notices)
    new = count["open"] + count["worse"]
    parts = [("%d problem%s" % (new, "s" * (new > 1))) if new else "", "%d still open" % count["remind"] if count["remind"] else "",
             "%d resolved" % count["resolved"] if count["resolved"] else "", ("%d event%s" % (len(events), "s" * (len(events) > 1))) if events else ""]
    bad = [level for kind, level, _, _ in notices if kind != "resolved"]
    cat = ("urgent" if CRIT in bad else "attention" if bad else "resolved" if not events
           else "security" if all(e.key.startswith("sec:") for e in events) else "attention")
    return headline(prefix, cat, host, ", ".join(p for p in parts if p), labels), lines + [tags(cat, host)], bool(new or events), cat


def report(prefix, host, readings, labels=None):
    """The daily report: every reading, bad ones first. It arriving at all says the monitor is alive."""
    bad = [r for r in readings if r.level != OK]
    up = (read("/proc/uptime") or "").split()
    lines = (["up %s" % dur(float(up[0]))] if up else []) + ["%s %s" % (ICON[r.level], r.text) for r in sorted(readings, key=lambda r: -RANK[r.level])]
    said = "%d of %d checks failing" % (len(bad), len(readings)) if bad else "all %d checks OK" % len(readings)
    return headline(prefix, "stats", host, "daily report, " + said, labels), lines + [tags("stats", host)], False, "stats"


# ---------- channels: each → (took it, why not) ----------
def plain(title, lines, limit=1900):
    """Plain text within the tightest limit of the webhook bots (WeCom: 2048 bytes)."""
    return (title + "\n" + "\n".join(lines)).encode("utf-8")[:limit].decode("utf-8", "ignore")


def answered(code, body, field=None, want=0):
    """A webhook's answer: 2xx, and when it answers JSON with a result code (`field`), that code."""
    if not 200 <= code < 300:
        return False, "HTTP %d %s" % (code, body[:160])
    if field:
        try:
            got = json.loads(body).get(field)
        except ValueError:
            return False, "answer is not JSON: %s" % body[:160]
        if got != want:
            return False, body[:160]
    return True, ""


def telegram(n, title, lines, loud):
    if not n.get("tg_chat_id"):
        return False, "tg_chat_id is not set (run: infrawatch.py chats)"
    url = "%s/bot%s/sendMessage" % ((n.get("tg_api_host") or "https://api.telegram.org").rstrip("/"), n["tg_bot_token"])
    base = {"chat_id": n["tg_chat_id"], "disable_web_page_preview": True, "disable_notification": not loud}
    if n.get("tg_thread_id"):
        base["message_thread_id"] = int(n["tg_thread_id"])  # a topic of a forum group
    rich = ("<b>%s</b>\n%s" % (html.escape(title, False), html.escape("\n".join(lines), False)))[:4000]
    code, body = http(url, dict(base, text=rich, parse_mode="HTML"))
    if code == 400:  # markup Telegram refuses (cut mid-tag) goes again as plain text
        code, body = http(url, dict(base, text=(title + "\n" + "\n".join(lines))[:4000]))
    return answered(code, body)


def wecom(n, title, lines, loud):
    return answered(*http("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + n["qywx_key"],
                          {"msgtype": "text", "text": {"content": plain(title, lines)}}), field="errcode")


def sign(key, msg):
    return base64.b64encode(hmac.new(key.encode(), msg.encode(), hashlib.sha256).digest()).decode()


def feishu(n, title, lines, loud):
    body = {"msg_type": "text", "content": {"text": plain(title, lines)}}
    if n.get("fssecret"):  # Feishu signs an empty message with "<seconds>\n<secret>" as the key
        ts = str(int(time.time()))
        body.update(timestamp=ts, sign=sign("%s\n%s" % (ts, n["fssecret"]), ""))
    host = (n.get("fs_host") or "https://open.feishu.cn").rstrip("/")  # Lark: https://open.larksuite.com
    return answered(*http("%s/open-apis/bot/v2/hook/%s" % (host, n["fskey"]), body), field="code")


def dingtalk(n, title, lines, loud):
    url = "https://oapi.dingtalk.com/robot/send?access_token=" + n["dd_bot_token"]
    if n.get("dd_bot_secret"):  # DingTalk signs "<milliseconds>\n<secret>" with the secret as the key
        ts = str(int(time.time() * 1000))
        url += "&timestamp=%s&sign=%s" % (ts, urllib.parse.quote_plus(sign(n["dd_bot_secret"], "%s\n%s" % (ts, n["dd_bot_secret"]))))
    return answered(*http(url, {"msgtype": "text", "text": {"content": plain(title, lines)}}), field="errcode")


def bark(n, title, lines, loud):
    server, _, key = n["bark_push"].rstrip("/").rpartition("/")  # "https://api.day.app/<key>" or the key alone
    return answered(*http((server or "https://api.day.app") + "/push",
                          {"device_key": key, "title": title, "body": "\n".join(lines)[:1900], "level": "timeSensitive" if loud else "passive"}))


def ntfy(n, title, lines, loud):
    head = {"Authorization": "Bearer " + n["ntfy_token"]} if n.get("ntfy_token") else {}
    return answered(*http((n.get("ntfy_url") or "https://ntfy.sh").rstrip("/") + "/",
                          {"topic": n["ntfy_topic"], "title": title, "message": "\n".join(lines)[:3500], "priority": 4 if loud else 2}, head))


def serverchan(n, title, lines, loud):
    key = n["push_key"]
    m = re.match(r"sctp(\d+)t", key)  # Server酱3 keys name their own host
    url = "https://%s.push.ft07.com/send/%s.send" % (m.group(1), key) if m else "https://sctapi.ftqq.com/%s.send" % key
    return answered(*http(url, {"title": title[:100], "desp": "\n\n".join(lines)[:1900]}, form=True), field="code")


def pushplus(n, title, lines, loud):
    return answered(*http("https://www.pushplus.plus/send", {"token": n["push_plus_token"], "title": title[:100], "content": "\n".join(lines)[:1900], "template": "txt"}),
                    field="code", want=200)


def webhook(n, title, lines, loud):
    return answered(*http(n["webhook_url"], {"title": title, "body": "\n".join(lines), "level": "alert" if loud else "info",
                                             "category": n.get("category", ""), "source": "infrawatch"}))


CHANNELS = [("telegram", "tg_bot_token", telegram), ("wecom", "qywx_key", wecom), ("feishu", "fskey", feishu),
            ("dingtalk", "dd_bot_token", dingtalk), ("bark", "bark_push", bark), ("ntfy", "ntfy_topic", ntfy),
            ("serverchan", "push_key", serverchan), ("pushplus", "push_plus_token", pushplus), ("webhook", "webhook_url", webhook)]


def send(n, title, lines, loud=True, say=None, cat=""):
    """To every configured channel → True when at least one took it. Failures go to stderr (the journal), never with
    a token in them; `say` gets one line a channel (the test command)."""
    took, any_set = False, False
    if cat:  # a category with its own Telegram topic goes there
        n = dict(n, category=cat, tg_thread_id=n.get("tg_thread_" + cat) or n.get("tg_thread_id", ""))
    for name, key, fn in CHANNELS:
        if not n.get(key):
            continue
        any_set = True
        try:
            good, why = fn(n, title, lines, loud)
        except Exception as e:
            good, why = False, "%s: %s" % (type(e).__name__, e)
        took = took or good
        if say:
            say("%-10s %s" % (name, "ok" if good else "FAILED  " + why))
        elif not good:
            print("infrawatch: %s did not take the message: %s" % (name, why), file=sys.stderr)
    if not any_set:
        (say or (lambda s: print("infrawatch: " + s, file=sys.stderr)))("no channel is configured in [notify]")
    return took


# ---------- one run ----------
def load_config(path):
    """config.ini, then the secrets: KEY=VALUE lines of notify.env beside it (so config.ini can be read and shown
    freely), then the environment, which wins. Keys are [notify]'s in capitals (TG_BOT_TOKEN=… as 青龙 scripts name
    them, TG_USER_ID for the chat id too) plus HEARTBEAT_URL / HEARTBEAT_TOKEN."""
    cfg = configparser.ConfigParser(interpolation=None, strict=False)  # a section written twice is merged, not fatal
    cfg.read_dict(DEFAULTS)
    found = cfg.read(path, encoding="utf-8")
    secret_files = [path] if any(v for k, v in cfg["notify"].items() if k != "prefix") or cfg["heartbeat"]["token"] else []
    env_file = os.path.join(os.path.dirname(path) or ".", "notify.env")
    given = {}
    for line in (read(env_file) or "").splitlines():
        k, eq, v = line.strip().partition("=")
        if eq and not k.startswith("#"):
            given[k.strip().upper()] = v.strip().strip("'\"")
    if given:
        secret_files.append(env_file)
    given.update((k, v) for k, v in os.environ.items() if v)
    for key in DEFAULTS["notify"]:
        if given.get(key.upper()):
            cfg["notify"][key] = given[key.upper()]
    if given.get("TG_USER_ID") and not cfg["notify"]["tg_chat_id"]:
        cfg["notify"]["tg_chat_id"] = given["TG_USER_ID"]
    for key in ("url", "token"):
        if given.get("HEARTBEAT_" + key.upper()):
            cfg["heartbeat"][key] = given["HEARTBEAT_" + key.upper()]
    return cfg, bool(found), secret_files


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}  # first run, or a file cut short: start over


def save_state(path, st):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(path + ".tmp", path)


def gather(cfg, st, now):
    readings, events = [], []
    for check in CHECKS:
        try:
            found = check(cfg, st, now)
        except Exception as e:  # one broken check never stops the others; it is itself a reading
            found = [R("check:" + check.__name__, WARN, "%s could not run: %s" % (check.__name__, e))]
        for x in found:
            (events if isinstance(x, E) else readings).append(x)
    return readings, events


def run(cfg, st, now, force_report=False):
    """One monitoring run: read, compare, send. st is updated in place and saved by the caller."""
    a, n, prefix, labels = cfg["alerting"], cfg["notify"], cfg["notify"]["prefix"], cfg["labels"]
    host = cfg["host"]["name"] or socket.gethostname()
    readings, events = gather(cfg, st, now)
    notices = evaluate(st, readings, now, a.getint("fail_threshold"), a.getint("ok_threshold"), a.getfloat("remind_hours") * 3600)
    queue = st.pop("outbox", [])  # what an earlier run could not send goes first
    if now < st.get("silence_until", 0):
        st["silenced"] = True  # state keeps moving; nothing is sent
    else:
        if st.pop("silenced", False):  # the silence just ended: say what is still wrong
            told = set(key for _, _, key, _ in notices)
            notices += [("remind", c["level"], k, c.get("text", k)) for k, c in st["checks"].items() if c["level"] != OK and k not in told]
        if notices or events:
            queue.append(compose(prefix, host, notices, events, labels))
        lt, at = time.localtime(now), cfg["host"]["daily_report"].strip()  # "08:00" in the machine's own time zone
        today, (hh, _, mm) = time.strftime("%Y-%m-%d", lt), at.partition(":")
        if force_report or (at and st.get("report_day") != today and (lt.tm_hour, lt.tm_min) >= (int(hh), int(mm or 0))):
            st["report_day"] = today
            queue.append(report(prefix, host, readings, labels))
    for title, lines, loud, *cat in queue:  # a message kept by an older version has no category
        if not send(n, title, lines, loud, cat="".join(cat)):
            st.setdefault("outbox", []).append([title, lines, loud] + list(cat))
    st["outbox"] = st.get("outbox", [])[-20:]
    st["last_run"] = now
    hb = cfg["heartbeat"]
    if hb["url"]:  # proof of life for whoever watches this host (the Cloudflare Worker, healthchecks.io, …)
        bad = sorted(((k, c) for k, c in st["checks"].items() if c["level"] != OK), key=lambda kc: -RANK[kc[1]["level"]])
        # The open alerts go along, so whoever takes the heartbeat can hand them to an agent (the Worker's
        # /incidents). None while silenced: a state caused on purpose is not something to act on.
        alerts = [] if st.get("silenced") else [{"key": k, "level": c["level"], "text": c.get("text", k)[:200], "since": int(c.get("since", now))} for k, c in bad[:30]]
        code, body = http(hb["url"], {"host": host, "open": len(bad), "crit": sum(c["level"] == CRIT for _, c in bad), "alerts": alerts},
                          {"Authorization": "Bearer " + hb["token"]} if hb["token"] else None)
        if not 200 <= code < 300:
            print("infrawatch: heartbeat not taken: HTTP %d %s" % (code, body[:120]), file=sys.stderr)
    return readings, events, notices


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # a C locale must not turn an emoji into a crash
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="infrawatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "check", "test", "chats", "status", "report", "silence"])
    ap.add_argument("arg", nargs="?", help="silence: a duration (30m, 2h, 1d) or off")
    ap.add_argument("--config", default="/etc/infrawatch/config.ini")
    ap.add_argument("--state", default="/var/lib/infrawatch/state.json")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)
    try:
        cfg, found, secret_files = load_config(args.config)
    except configparser.Error as e:
        print("infrawatch: %s cannot be read: %s" % (args.config, str(e).replace("\n", " ")), file=sys.stderr)
        return 2
    if not found and args.command != "check":
        print("infrawatch: no config at %s (copy config.example.ini there)" % args.config, file=sys.stderr)
        return 2
    for path in secret_files:
        if os.name == "posix" and os.stat(path).st_mode & 0o077:
            print("infrawatch: %s holds tokens and other users can read it: chmod 600 it" % path, file=sys.stderr)
    st, now, n = load_state(args.state), time.time(), cfg["notify"]
    host = cfg["host"]["name"] or socket.gethostname()

    if args.command == "check":
        readings, events = gather(cfg, st, now)
        for r in sorted(readings, key=lambda r: -RANK[r.level]):
            print("%s %-22s %s" % (ICON[r.level], r.key, r.text))
        for e in events:
            print("⚡ %-22s %s" % (e.key, e.text))
        print("\n%d readings, %d not OK, %d events. Channels configured: %s. Nothing was sent or saved." % (
            len(readings), sum(r.level != OK for r in readings), len(events), ", ".join(name for name, key, _ in CHANNELS if n.get(key)) or "none"))
        return 0
    if args.command == "test":
        ok = send(n, "%s ✅ %s: test message" % (n["prefix"], host), ["If you can read this, alerts from %s will reach you here." % host], True, print)
        return 0 if ok else 1
    if args.command == "chats":
        if not n["tg_bot_token"]:
            print("tg_bot_token is not set in [notify]")
            return 2
        code, body = http("%s/bot%s/getUpdates" % ((n["tg_api_host"] or "https://api.telegram.org").rstrip("/"), n["tg_bot_token"]))
        if code != 200:
            print("Telegram answered HTTP %d %s" % (code, body[:200]))
            return 1
        chats = {}
        for u in json.loads(body).get("result", []):
            for kind in ("message", "channel_post", "my_chat_member", "edited_message"):
                c = (u.get(kind) or {}).get("chat")
                if c:
                    chats[c["id"]] = "%-11s %s" % (c.get("type", "?"), c.get("title") or c.get("username") or c.get("first_name") or "")
                    thread = (u.get(kind) or {}).get("message_thread_id")
                    if thread:
                        chats[c["id"]] += "   (topic id %s)" % thread
        for cid, what in chats.items():
            print("%-16s %s" % (cid, what))
        if not chats:
            print("No chat yet. Write to the bot (or post in the channel it administers, or send /start@YourBot in the group), then run this again.")
        return 0
    if args.command == "status":
        bad = sorted(((c["level"], k, c) for k, c in st.get("checks", {}).items() if c["level"] != OK), key=lambda x: -RANK[x[0]])
        for level, k, c in bad:
            print("%s %s (for %s)" % (ICON[level], c.get("text", k), dur(now - c.get("since", now))))
        left = st.get("silence_until", 0) - now
        print("%d open alert%s%s; last run %s ago" % (len(bad), "" if len(bad) == 1 else "s", ", silenced for %s more" % dur(left) if left > 0 else "",
                                                      dur(now - st["last_run"]) if st.get("last_run") else "never"))
        return 0
    if args.command == "silence":
        m = re.match(r"^(\d+)([mhd])$", args.arg or "")
        if not m and args.arg != "off":
            print("usage: infrawatch.py silence 30m | 2h | 1d | off")
            return 2
        st["silence_until"] = now + int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else 0
        save_state(args.state, st)
        print("silenced for %s: still watching, nothing sent" % args.arg if m else "silence off")
        return 0
    run(cfg, st, now, force_report=args.command == "report")
    save_state(args.state, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
