"""Tests for skills/monitor-hosts/scripts/infrawatch.py. Standard library only:

    python -m unittest discover -s tests -v

The Linux collectors are fed real command output (df, /proc, systemctl, docker, nvidia-smi, ss, journalctl) through
the module's sh/read/statvfs hooks, so the suite runs on any OS. The end-to-end tests talk to a local HTTP server
standing in for Telegram and the heartbeat receiver.
"""
import contextlib
import http.server
import importlib.util
import io
import json
import os
import tempfile
import threading
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("infrawatch", os.path.join(HERE, "..", "skills", "monitor-hosts", "scripts", "infrawatch.py"))
iw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iw)
R, E, OK, WARN, CRIT = iw.R, iw.E, iw.OK, iw.WARN, iw.CRIT


def config(text=""):
    cfg = iw.configparser.ConfigParser(interpolation=None)
    cfg.read_dict(iw.DEFAULTS)
    cfg.read_string(text)
    return cfg


class Patched(unittest.TestCase):
    """Replaces the module's hooks for one test: commands by their first words, files by path."""
    def setUp(self):
        self.saved = {k: getattr(iw, k) for k in ("sh", "read", "http", "statvfs", "loadavg", "file_hashes")}
        self.cmds, self.files = {}, {}
        iw.sh = lambda *cmd, **kw: next((v for k, v in self.cmds.items() if " ".join(cmd).startswith(k)), (127, ""))
        iw.read = lambda path: self.files.get(path)
        iw.loadavg = None  # the machine running the tests has no say in them

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(iw, k, v)


class Grade(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(iw.grade(84, 85, 95, OK, 3), OK)
        self.assertEqual(iw.grade(85, 85, 95, OK, 3), WARN)
        self.assertEqual(iw.grade(95, 85, 95, OK, 3), CRIT)

    def test_an_open_alert_clears_only_below_the_margin(self):
        self.assertEqual(iw.grade(83, 85, 95, WARN, 3), WARN)  # still inside the margin
        self.assertEqual(iw.grade(81, 85, 95, WARN, 3), OK)
        self.assertEqual(iw.grade(93, 85, 95, CRIT, 3), CRIT)
        self.assertEqual(iw.grade(91, 85, 95, CRIT, 3), WARN)


class Evaluate(unittest.TestCase):
    def feed(self, st, level, now, **kw):
        return iw.evaluate(st, [R("x", level, "x is %s" % level, **kw)], now)

    def test_opens_after_three_bad_and_resolves_after_two_good(self):
        st = {}
        self.assertEqual(self.feed(st, CRIT, 0), [])
        self.assertEqual(self.feed(st, CRIT, 60), [])
        self.assertEqual(self.feed(st, CRIT, 120), [("open", CRIT, "x", "x is crit")])
        self.assertEqual(self.feed(st, CRIT, 180), [])  # no repeat while it stays down
        self.assertEqual(self.feed(st, OK, 240), [])
        self.assertEqual(self.feed(st, OK, 300), [("resolved", OK, "x", "x is ok (after 3m)")])
        self.assertEqual(st["checks"]["x"], {"level": OK, "bad": 0, "good": 0})

    def test_one_bad_minute_tells_nobody(self):
        st = {}
        for level in (CRIT, OK, CRIT, OK, CRIT, CRIT, OK):
            self.assertEqual(self.feed(st, level, 0), [])

    def test_a_flap_while_open_does_not_resolve(self):
        st = {}
        for now in (0, 60, 120):
            self.feed(st, CRIT, now)
        self.assertEqual(self.feed(st, OK, 180), [])
        self.assertEqual(self.feed(st, CRIT, 240), [])
        self.assertEqual(self.feed(st, OK, 300), [])  # the count of good readings started again

    def test_worse_is_told_at_once_and_better_is_quiet(self):
        st = {}
        for now in (0, 60, 120):
            self.feed(st, WARN, now)
        self.assertEqual(self.feed(st, CRIT, 180), [("worse", CRIT, "x", "x is crit")])
        self.assertEqual(self.feed(st, WARN, 240), [])

    def test_critical_is_told_again_after_the_reminder_interval_and_warning_never(self):
        st = {}
        for now in (0, 60, 120):
            self.feed(st, CRIT, now)
        self.assertEqual(self.feed(st, CRIT, 120 + 4 * 3600 - 1), [])
        self.assertEqual(self.feed(st, CRIT, 120 + 4 * 3600), [("remind", CRIT, "x", "x is crit (for 4h 0m)")])
        st = {}
        for now in (0, 60, 120, 10 * 3600):
            notices = self.feed(st, WARN, now)
        self.assertEqual(notices, [])

    def test_instant_readings_open_and_close_on_one_reading(self):
        st = {}
        self.assertEqual(self.feed(st, WARN, 0, instant=True)[0][0], "open")
        self.assertEqual(self.feed(st, OK, 60, instant=True)[0][0], "resolved")

    def test_a_check_no_longer_read_is_forgotten(self):
        st = {}
        iw.evaluate(st, [R("a", OK, ""), R("b", OK, "")], 0)
        iw.evaluate(st, [R("a", OK, "")], 60)
        self.assertEqual(list(st["checks"]), ["a"])


class Compose(unittest.TestCase):
    def test_title_counts_and_loudness(self):
        title, lines, loud = iw.compose("[iw]", "vps-01", [("open", CRIT, "a", "disk /: 96% used"), ("resolved", OK, "b", "memory: 40% used (after 12m)")], [E("oom", "out of memory, killed: node")])
        self.assertEqual(title, "[iw] 🔴 vps-01: 1 problem, 1 resolved, 1 event")
        self.assertEqual(lines, ["🔴 disk /: 96% used", "✅ memory: 40% used (after 12m)", "⚡ out of memory, killed: node"])
        self.assertTrue(loud)

    def test_a_recovery_alone_is_quiet(self):
        title, _, loud = iw.compose("[iw]", "h", [("resolved", OK, "b", "x")], [])
        self.assertEqual(title, "[iw] ✅ h: 1 resolved")
        self.assertFalse(loud)


class Parsers(unittest.TestCase):
    def test_mounts_keeps_real_filesystems_once(self):
        text = ("sysfs /sys sysfs rw 0 0\n/dev/sda1 / ext4 rw 0 0\n/dev/sda1 /var/lib/docker/overlay2/x ext4 rw 0 0\n"
                "tmpfs /run tmpfs rw 0 0\n/dev/loop3 /snap/core/1 squashfs ro 0 0\n/dev/nvme0n1p2 /data\\040disk xfs rw 0 0\ntank/media /tank zfs rw 0 0\n")
        self.assertEqual(iw.mounts(text), {"/dev/sda1": "/", "/dev/nvme0n1p2": "/data disk", "tank/media": "/tank"})

    def test_meminfo(self):
        m = iw.meminfo("MemTotal:       16314508 kB\nMemAvailable:    4078627 kB\nHugePages_Total:       0\n")
        self.assertEqual((m["MemTotal"], m["MemAvailable"], m["HugePages_Total"]), (16314508, 4078627, 0))

    def test_gpus_with_values_the_card_does_not_report(self):
        cards = iw.gpus("0, 54, 12, 3120, 16376, NVIDIA RTX 5000 Ada Generation\n1, [N/A], [N/A], 10, 24564, NVIDIA GeForce RTX 4090, rev a1\n")
        self.assertEqual(cards[0], {"i": "0", "temp": 54.0, "util": 12.0, "used": 3120.0, "total": 16376.0, "name": "NVIDIA RTX 5000 Ada Generation"})
        self.assertEqual((cards[1]["temp"], cards[1]["util"], cards[1]["name"]), (None, None, "NVIDIA GeForce RTX 4090, rev a1"))

    def test_listening_leaves_loopback_out(self):
        text = ("LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 511 [::]:443 [::]:*\n"
                "LISTEN 0 128 [::1]:6010 [::]:*\nLISTEN 0 4096 *:8080 *:*\n")
        self.assertEqual(iw.listening(text), {"0.0.0.0:22", "[::]:443", "*:8080"})

    def test_durations_and_sizes(self):
        self.assertEqual([iw.dur(s) for s in (45, 720, 14520, 183600)], ["45s", "12m", "4h 2m", "2d 3h"])
        self.assertEqual([iw.size(n) for n in (512, 5 * 1024 ** 2, 12.5 * 1024 ** 3)], ["512 B", "5 MB", "12.5 GB"])

    def test_plain_text_is_cut_on_a_character_boundary(self):
        text = iw.plain("tiêu đề", ["ổ đĩa đầy" * 400])
        self.assertLessEqual(len(text.encode("utf-8")), 1900)
        text.encode("utf-8").decode("utf-8")  # still valid


class Collectors(Patched):
    def test_disks(self):
        self.files["/proc/mounts"] = "/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /data ext4 rw 0 0\n/dev/sdc1 /boot/efi vfat rw 0 0\n"
        full = types.SimpleNamespace(f_blocks=1000, f_bfree=40, f_bavail=30, f_frsize=4096, f_files=1000, f_ffree=900)
        roomy = types.SimpleNamespace(f_blocks=1000, f_bfree=600, f_bavail=550, f_frsize=4096, f_files=0, f_ffree=0)
        iw.statvfs = lambda mnt: full if mnt == "/" else roomy
        got = {r.key: r for r in iw.check_disks(config(), {}, 0)}
        self.assertEqual(sorted(got), ["disk:/", "disk:/data", "inode:/"])  # /boot/efi ignored; no inode count on /data
        self.assertEqual((got["disk:/"].level, got["disk:/"].text), (CRIT, "disk /: 97% used, 120 KB free"))
        self.assertEqual((got["disk:/data"].level, got["inode:/"].level), (OK, OK))

    def test_memory_and_swap(self):
        self.files["/proc/meminfo"] = "MemTotal: 1000 kB\nMemAvailable: 80 kB\nSwapTotal: 1000 kB\nSwapFree: 900 kB\n"
        got = {r.key: r.level for r in iw.check_memory(config(), {}, 0)}
        self.assertEqual(got, {"memory": WARN, "swap": OK})

    def test_load_is_per_core(self):
        iw.loadavg = lambda: (9.0, 2.0 * (os.cpu_count() or 1), 0.1)
        self.assertEqual([(r.level, r.text) for r in iw.check_load(config(), {}, 0)], [(WARN, "load: 2.00 per core (5 min)")])

    def test_units(self):
        self.cmds["systemctl is-active"] = (3, "active\ninactive\n")
        self.cmds["systemctl list-units"] = (0, "certbot.service loaded failed failed Certbot\nfoo.service loaded failed failed Foo\n")
        got = {r.key: (r.level, r.text) for r in iw.check_units(config("[services]\nunits = nginx, postgresql"), {}, 0)}
        self.assertEqual(got["unit:nginx"][0], OK)
        self.assertEqual(got["unit:postgresql"], (CRIT, "service postgresql: inactive"))
        self.assertEqual(got["units:failed"], (WARN, "failed units: certbot.service, foo.service"))

    def test_containers(self):
        self.cmds["docker ps"] = (0, "thumbs\trunning\tUp 3 days\ndb\texited\tExited (1) 2 hours ago\nweb\trunning\tUp 5 minutes (unhealthy)\n")
        got = {r.key: (r.level, r.text) for r in iw.check_containers(config("[services]\ncontainers = thumbs, db, gone"), {}, 0)}
        self.assertEqual(got["container:thumbs"][0], OK)
        self.assertEqual(got["container:db"], (CRIT, "container db: exited"))
        self.assertEqual(got["container:gone"], (CRIT, "container gone: missing"))
        self.assertEqual(got["containers:unhealthy"], (WARN, "containers restarting or unhealthy: web"))

    def test_no_docker_is_only_a_problem_when_containers_are_asked_for(self):
        self.assertEqual(iw.check_containers(config(), {}, 0), [])
        self.assertEqual(iw.check_containers(config("[services]\ncontainers = a"), {}, 0)[0].level, CRIT)

    def test_http(self):
        answers = {"https://up/": (200, "status: ok"), "https://down/": (502, ""), "https://dead/": (0, "timed out")}
        iw.http = lambda url, *a, **kw: answers[url]
        cfg = config("[http]\nup = https://up/ | ok\nwrong = https://up/ | healthy\ndown = https://down/\ndead = https://dead/")
        got = {r.key: (r.level, r.text) for r in iw.check_http(cfg, {}, 0)}
        self.assertEqual(got["http:up"], (OK, "http up: HTTP 200"))
        self.assertEqual(got["http:wrong"], (CRIT, "http wrong: HTTP 200 but the answer lacks 'healthy'"))
        self.assertEqual(got["http:down"], (CRIT, "http down: HTTP 502"))
        self.assertEqual(got["http:dead"], (CRIT, "http dead: no answer (timed out)"))

    def test_gpu(self):
        self.cmds["nvidia-smi --query-gpu"] = (0, "0, 93, 99, 16000, 16376, NVIDIA RTX 5000 Ada Generation\n")
        self.cmds["nvidia-smi --query-compute-apps"] = (0, "/usr/bin/python3\n")
        st = {}
        got = {r.key: (r.level, r.text) for r in iw.check_gpu(config("[gpu]\nprocesses = python3, ffmpeg"), st, 0)}
        self.assertEqual(got["gpu0:temp"], (CRIT, "GPU 0 (NVIDIA RTX 5000 Ada Generation): 93°C"))
        self.assertEqual(got["gpu0:mem"], (WARN, "GPU 0 memory: 98% (15.6 of 16.0 GB)"))
        self.assertEqual((got["gpu:proc:python3"][0], got["gpu:proc:ffmpeg"][0]), (OK, CRIT))
        self.assertEqual(st["gpus"], 1)

    def test_gpu_absent_is_silent_until_one_was_seen(self):
        self.assertEqual(iw.check_gpu(config(), {}, 0), [])  # no driver, auto mode
        self.assertEqual([r.key for r in iw.check_gpu(config(), {"gpus": 1}, 0)], ["gpu:smi"])  # it used to answer
        self.assertEqual(iw.check_gpu(config("[gpu]\nenabled = true"), {}, 0)[0].level, CRIT)

    def test_gpu_count_drop_and_idle(self):
        self.cmds["nvidia-smi --query-gpu"] = (0, "0, 40, 1, 100, 16376, RTX\n")
        cfg, st = config("[gpu]\nidle_minutes = 30"), {"gpus": 2}
        got = {r.key: r for r in iw.check_gpu(cfg, st, 1000)}
        self.assertEqual((got["gpu:count"].level, got["gpu:count"].text), (CRIT, "1 of 2 GPUs visible"))
        self.assertEqual(got["gpu:idle"].level, OK)  # idle only just now
        got = {r.key: r for r in iw.check_gpu(cfg, st, 1000 + 1800)}
        self.assertEqual((got["gpu:idle"].level, got["gpu:idle"].text, got["gpu:idle"].instant), (WARN, "GPU idle for 30m (under 5% use)", True))
        self.cmds["nvidia-smi --query-gpu"] = (0, "0, 70, 96, 9000, 16376, RTX\n")
        self.assertEqual({r.key: r for r in iw.check_gpu(cfg, st, 4000)}["gpu:idle"].level, OK)

    def test_kernel_events(self):
        self.files["/proc/sys/kernel/random/boot_id"] = "boot-2\n"
        self.files["/proc/uptime"] = "125.5 400.0\n"
        self.cmds["journalctl -k"] = (0, "Out of memory: Killed process 4242 (node) total-vm:1kB\nMemory cgroup out of memory: Killed process 4243 (node) x\n"
                                         "Out of memory: Killed process 99 (python3) x\nNVRM: Xid (PCI:0000:01:00): 79, pid=1, GPU has fallen off the bus.\nNVRM: Xid (PCI:0000:01:00): 13, pid=7, Graphics Exception\n")
        st = {"boot": "boot-1", "last_run": 1000}
        texts = [e.text for e in iw.check_kernel(config(), st, 1060)]
        self.assertEqual(texts, ["host rebooted, up 2m", "out of memory, killed: node ×2, python3", "GPU Xid 13 on PCI:0000:01:00",
                                 "GPU Xid 79 on PCI:0000:01:00 (usually needs a GPU reset or reboot)"])
        self.assertEqual(st["boot"], "boot-2")
        self.cmds.clear()
        self.assertEqual(iw.check_kernel(config(), {}, 0), [])  # a first run has no boot to compare with

    def test_security_learns_then_tells_only_what_is_new(self):
        self.cmds["ss -H -tln"] = (0, "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n")
        self.cmds["docker ps"] = (0, "thumbs\n")
        self.cmds["journalctl -u ssh"] = (0, "Accepted publickey for ubuntu from 10.0.0.5 port 50000 ssh2: ED25519 SHA256:aaa\n")
        cfg, st = config("[security]\nenabled = true"), {}
        iw.file_hashes = lambda globs: {"/etc/passwd": "h1"}
        self.assertEqual([e.text for e in iw.check_security(cfg, st, 100)], ["security baseline learned: 1 files, 1 public ports, 1 containers"])
        self.assertEqual(iw.check_security(cfg, st, 160), [])  # the same login again: known
        self.cmds["ss -H -tln"] = (0, "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:4444 0.0.0.0:*\n")
        self.cmds["docker ps"] = (0, "thumbs\nminer\n")
        self.cmds["journalctl -u ssh"] = (0, "Accepted publickey for ubuntu from 203.0.113.9 port 1 ssh2: ED25519 SHA256:bbb\nAccepted password for root from 10.0.0.5 port 2 ssh2\n")
        iw.file_hashes = lambda globs: {"/etc/passwd": "h2", "/etc/cron.d/x": "h3"}
        self.assertEqual([e.text for e in iw.check_security(cfg, st, 220)], [
            "new file: /etc/cron.d/x", "file changed: /etc/passwd", "new public listening port: 0.0.0.0:4444", "new container: miner",
            "SSH login: ubuntu from 203.0.113.9 (publickey, new key)", "SSH login: root from 10.0.0.5 (password)"])
        self.cmds["journalctl -u ssh"] = (0, "Failed password for root from 198.51.100.1 port 1 ssh2\n" * 25)
        self.assertEqual([e.text for e in iw.check_security(cfg, st, 280)], ["25 failed SSH logins in 1m"])

    def test_file_hashes_reads_real_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.conf"), "w") as f:
                f.write("x")
            os.mkdir(os.path.join(d, "sub.conf"))
            got = iw.file_hashes([os.path.join(d, "*.conf"), os.path.join(d, "missing")])
        self.assertEqual(list(got.values()), ["2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"])  # sha256("x")

    def test_security_is_off_by_default(self):
        self.assertEqual(iw.check_security(config(), {}, 0), [])

    def test_a_broken_check_is_a_reading_not_a_crash(self):
        iw.statvfs = None  # as on a system without it
        self.files["/proc/mounts"] = "/dev/sda1 / ext4 rw 0 0\n"
        readings, _ = iw.gather(config(), {}, 0)
        self.assertIn(("check:check_disks", WARN), [(r.key, r.level) for r in readings])


class Channels(Patched):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.answers = []  # consumed in order; then 200 {}
        iw.http = self.fake_http

    def fake_http(self, url, data=None, headers=None, timeout=10, form=False):
        self.sent.append((url, data, headers, form))
        return self.answers.pop(0) if self.answers else (200, '{"errcode": 0, "code": 0}')

    def test_telegram_html_then_plain_when_refused(self):
        n = {"tg_bot_token": "T", "tg_chat_id": "-1001", "tg_thread_id": "7"}
        self.answers = [(400, '{"ok":false,"description":"can\'t parse entities"}'), (200, '{"ok":true}')]
        self.assertEqual(iw.telegram(n, "a <b> & c", ["x < y"], False), (True, ""))
        (url, first, _, _), (_, second, _, _) = self.sent
        self.assertEqual(url, "https://api.telegram.org/botT/sendMessage")
        self.assertEqual((first["text"], first["parse_mode"]), ("<b>a &lt;b&gt; &amp; c</b>\nx &lt; y", "HTML"))
        self.assertEqual((first["chat_id"], first["message_thread_id"], first["disable_notification"]), ("-1001", 7, True))
        self.assertEqual((second["text"], "parse_mode" in second), ("a <b> & c\nx < y", False))

    def test_telegram_failure_says_why_without_the_token(self):
        self.answers = [(401, '{"ok":false,"error_code":401,"description":"Unauthorized"}')]
        ok, why = iw.telegram({"tg_bot_token": "SECRET", "tg_chat_id": "1"}, "t", [], True)
        self.assertFalse(ok)
        self.assertIn("401", why)
        self.assertNotIn("SECRET", why)
        self.assertEqual(iw.telegram({"tg_bot_token": "T"}, "t", [], True)[0], False)  # no chat id

    def test_webhook_bots(self):
        n = {"qywx_key": "K", "fskey": "F", "fssecret": "S", "dd_bot_token": "D", "dd_bot_secret": "S2", "push_key": "sctp123tabc", "push_plus_token": "P",
             "bark_push": "https://bark.example/KEY", "ntfy_topic": "ops", "ntfy_token": "tk", "webhook_url": "https://hook.example/x"}
        self.answers = [(200, '{"errcode":0}'), (200, '{"code":0}'), (200, '{"errcode":0}'), (200, '{}'), (200, '{}'), (200, '{"code":0}'), (200, '{"code":200}'), (204, "")]
        for fn in (iw.wecom, iw.feishu, iw.dingtalk, iw.bark, iw.ntfy, iw.serverchan, iw.pushplus, iw.webhook):
            self.assertEqual(fn(n, "title", ["line"], True), (True, ""), fn.__name__)
        wecom, feishu, ding, bark, ntfy, sc, pp, hook = self.sent
        self.assertEqual((wecom[0], wecom[1]), ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K", {"msgtype": "text", "text": {"content": "title\nline"}}))
        self.assertEqual(feishu[0], "https://open.feishu.cn/open-apis/bot/v2/hook/F")
        self.assertEqual(feishu[1]["sign"], iw.sign("%s\nS" % feishu[1]["timestamp"], ""))
        self.assertRegex(ding[0], r"^https://oapi\.dingtalk\.com/robot/send\?access_token=D&timestamp=\d{13}&sign=[\w%]+$")
        self.assertEqual((bark[0], bark[1]["device_key"], bark[1]["level"]), ("https://bark.example/push", "KEY", "timeSensitive"))
        self.assertEqual((ntfy[0], ntfy[1]["topic"], ntfy[2]), ("https://ntfy.sh/", "ops", {"Authorization": "Bearer tk"}))
        self.assertEqual((sc[0], sc[3]), ("https://123.push.ft07.com/send/sctp123tabc.send", True))
        self.assertEqual(pp[1]["token"], "P")
        self.assertEqual(hook[1], {"title": "title", "body": "line", "level": "alert", "source": "infrawatch"})

    def test_signatures_match_the_documented_algorithms(self):
        # HMAC-SHA256 then base64. Feishu: key "<ts>\n<secret>", empty message. DingTalk: key secret, message "<ts>\n<secret>".
        # Expected values computed apart from this code, with Node's crypto.createHmac("sha256", key).update(msg).digest("base64").
        self.assertEqual(iw.sign("1599360473\nsecret", ""), "q4jswNiMy51J5JuQV566yJat0/lQ/c+22kINzUgKsGU=")
        self.assertEqual(iw.sign("secret", "1599360473000\nsecret"), "h/3f4uEADBeKkVuXX2NS1pXSEqARbYb1RV0kmXPBfn0=")

    def test_a_bot_that_answers_an_error_code_did_not_take_it(self):
        self.answers = [(200, '{"errcode":310000,"errmsg":"keywords not in content"}')]
        ok, why = iw.dingtalk({"dd_bot_token": "D"}, "t", [], True)
        self.assertEqual((ok, "keywords" in why), (False, True))

    def test_send_is_true_when_any_channel_took_it(self):
        self.answers = [(500, "boom")]
        said = []
        self.assertTrue(iw.send({"tg_bot_token": "T", "tg_chat_id": "1", "webhook_url": "https://h/"}, "t", ["l"], True, said.append))
        self.assertEqual([s.split()[:2] for s in said], [["telegram", "FAILED"], ["webhook", "ok"]])
        self.assertFalse(iw.send({}, "t", ["l"], True, said.append))
        self.assertEqual(said[-1], "no channel is configured in [notify]")


class FakeTelegram(http.server.BaseHTTPRequestHandler):
    """Stands in for api.telegram.org and for the heartbeat receiver; fails while server.broken is set."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.got.append((self.path, body, self.headers.get("Authorization")))
        self.send_response(500 if self.server.broken else 200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


class EndToEnd(Patched):
    def setUp(self):
        super().setUp()
        iw.http = self.saved["http"]  # the real one, against the local server
        self.server = http.server.HTTPServer(("127.0.0.1", 0), FakeTelegram)
        self.server.got, self.server.broken = [], False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.files["/proc/meminfo"] = "MemTotal: 1000 kB\nMemAvailable: 500 kB\n"
        self.cfg = config("[host]\nname = vps-01\ndaily_report =\n[notify]\ntg_bot_token = T\ntg_chat_id = -100\ntg_api_host = %s\n"
                          "[heartbeat]\nurl = %s/hb/vps-01\ntoken = hbsecret\n[services]\nfailed_units = false\n[gpu]\nenabled = false" % (self.base, self.base))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def messages(self):
        return [body["text"] for path, body, _ in self.server.got if path.endswith("/sendMessage")]

    def test_alert_recovery_retry_and_heartbeat(self):
        st, low = {}, "MemTotal: 1000 kB\nMemAvailable: 20 kB\n"
        iw.run(self.cfg, st, 0)
        self.assertEqual(self.messages(), [])  # all fine on a first run: nothing to say
        self.assertEqual(self.server.got[-1], ("/hb/vps-01", {"host": "vps-01", "open": 0, "crit": 0}, "Bearer hbsecret"))
        self.files["/proc/meminfo"] = low
        iw.run(self.cfg, st, 60)
        iw.run(self.cfg, st, 120)
        self.assertEqual(self.messages(), [])
        self.server.broken = True  # Telegram is down exactly when the alert opens
        iw.run(self.cfg, st, 180)
        self.assertEqual(len(st["outbox"]), 1)
        self.server.broken = False
        self.server.got.clear()
        iw.run(self.cfg, st, 240)  # the kept message goes out with the next run
        self.assertEqual(self.messages(), ["<b>[infrawatch] 🔴 vps-01: 1 problem</b>\n🔴 memory: 98% used, 20 KB available"])
        self.assertEqual(st["outbox"], [])
        self.assertEqual(self.server.got[-1][1], {"host": "vps-01", "open": 1, "crit": 1})
        self.files["/proc/meminfo"] = "MemTotal: 1000 kB\nMemAvailable: 500 kB\n"
        iw.run(self.cfg, st, 300)
        iw.run(self.cfg, st, 360)
        self.assertEqual(self.messages()[-1], "<b>[infrawatch] ✅ vps-01: 1 resolved</b>\n✅ memory: 50% used, 500 KB available (after 3m)")

    def test_silence_holds_messages_then_says_what_is_still_wrong(self):
        st = {"silence_until": 500}
        self.files["/proc/meminfo"] = "MemTotal: 1000 kB\nMemAvailable: 20 kB\n"
        for now in (0, 60, 120, 180):
            iw.run(self.cfg, st, now)
        self.assertEqual(self.messages(), [])
        self.assertEqual(st["checks"]["memory"]["level"], CRIT)  # it kept watching
        iw.run(self.cfg, st, 600)
        self.assertEqual(self.messages(), ["<b>[infrawatch] 🔴 vps-01: 1 still open</b>\n🔴 still: memory: 98% used, 20 KB available"])

    def test_daily_report_once_a_day(self):
        self.cfg["host"]["daily_report"] = "00:00"
        st = {}
        iw.run(self.cfg, st, 1_700_000_000)
        iw.run(self.cfg, st, 1_700_000_060)
        self.assertEqual(self.messages(), ["<b>[infrawatch] 📊 vps-01: daily report, all 1 checks OK</b>\n✅ memory: 50% used, 500 KB available"])
        iw.run(self.cfg, st, 1_700_000_000 + 86400)
        self.assertEqual(len(self.messages()), 2)

    def test_commands(self):
        with tempfile.TemporaryDirectory() as d:
            conf, state = os.path.join(d, "config.ini"), os.path.join(d, "state", "state.json")
            with open(conf, "w", encoding="utf-8") as f:
                self.cfg.write(f)
            if os.name == "posix":
                os.chmod(conf, 0o600)

            def cli(*args):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = iw.main(list(args) + ["--config", conf, "--state", state])
                return code, out.getvalue()

            code, out = cli("check")
            self.assertEqual(code, 0)
            self.assertIn("memory: 50% used", out)
            self.assertIn("Channels configured: telegram. Nothing was sent or saved.", out)
            self.assertFalse(os.path.exists(state))
            self.assertEqual(cli("test"), (0, "telegram   ok\n"))
            self.assertIn("test message", self.messages()[-1])
            self.assertEqual(cli("run")[0], 0)
            self.assertTrue(os.path.exists(state))
            self.assertIn("0 open alerts; last run", cli("status")[1])
            self.assertEqual(cli("silence", "2h"), (0, "silenced for 2h: still watching, nothing sent\n"))
            self.assertRegex(cli("status")[1], r"silenced for (1h 59m|2h 0m) more")  # 2h 0m when the clock has not ticked since
            self.assertEqual(cli("silence", "soon")[0], 2)
            self.assertEqual(cli("silence", "off")[1], "silence off\n")
            self.assertEqual(iw.main(["run", "--config", os.path.join(d, "missing.ini"), "--state", state]), 2)
            with open(conf, "a", encoding="utf-8") as f:
                f.write("\n[tcp]\nselftest = 127.0.0.1:9\n")  # a section written a second time by hand is merged
            self.assertIn("tcp:selftest", cli("check")[1])
            with open(conf, "a", encoding="utf-8") as f:
                f.write("this line is not a setting\n")
            self.assertEqual(cli("check")[0], 2)  # a config that cannot be read stops with a message, not a traceback

    def test_the_example_config_is_valid_and_matches_the_defaults(self):
        cfg = iw.configparser.ConfigParser(interpolation=None, strict=False)
        cfg.read(os.path.join(HERE, "..", "skills", "monitor-hosts", "assets", "config.example.ini"), encoding="utf-8")
        for section in cfg.sections():
            self.assertIn(section, iw.DEFAULTS)
            for key, value in cfg[section].items():
                self.assertIn(key, iw.DEFAULTS[section], "%s.%s is not a setting" % (section, key))
                self.assertEqual(value, iw.DEFAULTS[section][key], "%s.%s differs from the built-in default" % (section, key))

    def test_secrets_come_from_notify_env_beside_the_config_and_the_environment_wins(self):
        iw.read = self.saved["read"]
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "config.ini")
            with open(conf, "w", encoding="utf-8") as f:
                f.write("[host]\nname = vps-01\n[notify]\nprefix = [ops]\n")
            cfg, found, secret_files = iw.load_config(conf)
            self.assertEqual((found, secret_files, cfg["notify"]["tg_bot_token"]), (True, [], ""))
            with open(os.path.join(d, "notify.env"), "w", encoding="utf-8") as f:
                f.write("# staged by setup-alert-channel\nTG_BOT_TOKEN=123:abc\nTG_USER_ID='-1001'\nQYWX_KEY=\nHEARTBEAT_URL=https://w.example/hb/vps-01\nHEARTBEAT_TOKEN=hb\n")
            os.environ["DD_BOT_TOKEN"] = "from-env"
            try:
                cfg, _, secret_files = iw.load_config(conf)
            finally:
                del os.environ["DD_BOT_TOKEN"]
            n = cfg["notify"]
            self.assertEqual((n["tg_bot_token"], n["tg_chat_id"], n["qywx_key"], n["dd_bot_token"], n["prefix"]), ("123:abc", "-1001", "", "from-env", "[ops]"))
            self.assertEqual((cfg["heartbeat"]["url"], cfg["heartbeat"]["token"]), ("https://w.example/hb/vps-01", "hb"))
            self.assertEqual(secret_files, [os.path.join(d, "notify.env")])


if __name__ == "__main__":
    unittest.main()
