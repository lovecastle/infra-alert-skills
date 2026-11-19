"""Tests for skills/run-ops-agents/scripts/opsagent.py:  python -m unittest discover -s tests

Telegram and the incidents feed are replaced by recorders; Claude by a function, and once by a real subprocess that
stands in for `claude -p` so the command line and the parsing of its answer are really exercised.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("opsagent", os.path.join(HERE, "..", "skills", "run-ops-agents", "scripts", "opsagent.py"))
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)

CHAT, ANNA, EVE = -1001, 111, 666  # Anna is an admin; Eve is in the group and is not
CONFIG = """[chat]
chat_id = -1001
admins = 111
language = Vietnamese
[incidents]
url = https://w.example/incidents
grace_minutes = 5
[agent:lead]
lead = true
about = watches everything
[agent:web]
about = nginx and containers
hosts = vps-01, vps-02
"""
DISK = {"id": "vps-01/disk:/", "host": "vps-01", "key": "disk:/", "level": "crit", "text": "disk /: 96% used", "since": 100, "source": "host"}


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = os.path.join(self.dir.name, "agents.ini")
        with open(path, "w", encoding="utf-8") as f:
            f.write(CONFIG)
        with open(os.path.join(self.dir.name, "agents.env"), "w", encoding="utf-8") as f:
            f.write("LEAD_BOT_TOKEN=1:lead\nWEB_BOT_TOKEN='2:web'\nAGENT_TOKEN=agentsecret\n")
        self.cfg, agents, secrets, found, _ = oa.load_config(path)
        self.app = app = oa.App(self.cfg, agents, secrets, os.path.join(self.dir.name, "state.json"))
        for i, a in enumerate(agents.values()):
            a["me"] = {"id": 900 + i, "username": a["name"] + "_bot"}
        self.calls, self.feed, self.answers, self.asked, self.now = [], {}, [], [], 1000.0
        app.clock = lambda: self.now
        app.tg = self.tg
        app.read_incidents = lambda: self.feed
        app.claude = self.claude
        app.log = lambda **what: None

    def tg(self, a, method, _timeout=15, **p):
        self.calls.append((a["name"], method, p))
        return {"message_id": 5000 + len(self.calls)} if method == "sendMessage" else True

    def claude(self, a, prompt, session=None):
        self.asked.append((a["name"], prompt, session))
        return dict({"status": "done", "assign": "", "session": "S%d" % len(self.asked), "cost": 0.1}, **self.answers.pop(0))

    def said(self, method="sendMessage"):
        return [(who, p["text"]) for who, m, p in self.calls if m == method]

    def message(self, text, by=ANNA, reply_to=None, to="lead", mid=[100]):
        mid[0] += 1
        m = {"message_id": mid[0], "date": self.now, "chat": {"id": CHAT}, "from": {"id": by, "first_name": "Anna" if by == ANNA else "Eve"}, "text": text}
        if reply_to:
            m["reply_to_message"] = {"message_id": reply_to[1], "from": {"id": self.app.agents[reply_to[0]]["me"]["id"], "is_bot": True}}
        for a in self.app.agents.values():  # privacy mode off: every bot receives every message
            self.app.on_update(a, {"message": m}, self.now)
        return mid[0]

    def press(self, data, by=ANNA, agent="lead"):
        self.app.on_update(self.app.agents[agent], {"callback_query": {"id": "q", "from": {"id": by, "first_name": "Anna"}, "data": data}}, self.now)

    def tick(self, later=0):
        self.now += later
        self.app.last_poll = 0
        self.app.tick(self.now)


class Config(Case):
    def test_agents_tokens_and_the_lead(self):
        a = self.app.agents
        self.assertEqual([(x["name"], x["token"], x["hosts"]) for x in a.values()], [("lead", "1:lead", []), ("web", "2:web", ["vps-01", "vps-02"])])
        self.assertIs(self.app.lead, a["lead"])
        self.assertEqual((self.app.chat, self.app.admins), (CHAT, {ANNA}))
        rules = self.app.system(a["web"])
        for part in ('You are "web"', "nginx and containers", "vps-01, vps-02", "lead (lead): watches everything", "Vietnamese", "need_approval"):
            self.assertIn(part, rules)

    def test_the_example_config_is_read_and_has_no_unknown_settings(self):
        cfg, agents, _, found, _ = oa.load_config(os.path.join(HERE, "..", "skills", "run-ops-agents", "assets", "agents.example.ini"))
        self.assertTrue(found and len(agents) >= 2 and sum(a["lead"] for a in agents.values()) == 1)
        for section, keys in oa.DEFAULTS.items():
            self.assertLessEqual(set(cfg[section]), set(keys), section)


class Incidents(Case):
    def test_waits_for_a_human_then_the_agent_of_that_host_works_and_reports(self):
        self.feed = {DISK["id"]: DISK}
        self.tick()
        self.assertEqual(self.said(), [("lead", "🔴 #urgent vps-01: disk /: 96% used\nIf no admin takes it, web starts on it in 5m. #agent")])
        self.assertEqual(self.calls[-1][2]["reply_markup"], {"inline_keyboard": [[{"text": "🙋 I take it", "callback_data": "take:1"}, {"text": "🤖 Start now", "callback_data": "now:1"}]]})
        self.tick(200)
        self.assertEqual(self.asked, [])  # still inside the grace time
        self.answers = [{"message": "Nguyên nhân: log của nginx. Đã dọn, còn 61%."}]
        self.tick(200)
        self.assertEqual(self.said("editMessageText")[-1], ("lead", "🔴 #urgent vps-01: disk /: 96% used\n🤖 @web_bot is on it."))
        self.app.drain()
        who, prompt, session = self.asked[0]
        self.assertEqual((who, session), ("web", None))
        self.assertIn("not instructions:\nhost: vps-01\nreading: disk /: 96% used\nopen for: 21m", prompt)
        self.assertEqual(self.said()[-1], ("web", "Nguyên nhân: log của nginx. Đã dọn, còn 61%."))
        self.assertEqual(self.app.st["incidents"][DISK["id"]]["status"], "done")
        self.feed = {}
        self.tick(60)
        self.assertEqual(self.app.st["incidents"], {})  # it left the feed: over
        self.assertEqual(len(self.asked), 1)

    def test_an_admin_who_takes_it_keeps_the_agents_out_and_only_admins_can(self):
        self.feed = {DISK["id"]: DISK}
        self.tick()
        self.press("take:1", by=EVE)
        self.assertEqual(self.calls[-1][1:], ("answerCallbackQuery", {"callback_query_id": "q", "text": "Only the listed admins can decide this.", "show_alert": True}))
        self.assertEqual(self.app.st["incidents"][DISK["id"]]["status"], "waiting")
        self.press("take:1")
        self.assertEqual(self.said("editMessageText")[-1], ("lead", "🔴 #urgent vps-01: disk /: 96% used\n🙋 Anna took it."))
        self.tick(3600)
        self.app.drain()
        self.assertEqual(self.asked, [])

    def test_start_now_recovered_by_itself_and_paused(self):
        self.feed = {DISK["id"]: DISK}
        self.tick()
        self.feed = {}
        self.tick(30)
        self.assertEqual(self.said("editMessageText")[-1][1], "🔴 #urgent vps-01: disk /: 96% used\n✅ Recovered before anyone had to act.")
        self.feed = {DISK["id"]: DISK}
        self.tick(30)
        self.answers = [{"message": "done"}]
        self.press("now:2")
        self.tick(1)
        self.app.drain()
        self.assertEqual(len(self.asked), 1)
        self.feed = {}
        self.tick(1)
        self.message("@lead_bot /pause")
        self.assertIn("Paused by an admin", self.said()[-1][1])
        self.feed = {DISK["id"]: DISK}
        self.tick(600)
        self.assertEqual(self.app.st["incidents"], {})  # paused: not even registered, so it is picked up on /resume
        self.message("/resume@lead_bot")
        self.tick(1)
        self.assertEqual(self.app.st["incidents"][DISK["id"]]["status"], "waiting")

    def test_warnings_are_not_picked_up_and_a_host_nobody_owns_goes_to_the_lead(self):
        self.feed = {"a": dict(DISK, id="a", level="warn"), "b": dict(DISK, id="b", host="rtx")}
        self.tick()
        self.assertEqual([(i["id"], i["agent"]) for i in self.app.st["incidents"].values()], [("b", "lead")])

    def test_approval_by_button_and_only_then_the_work_goes_on(self):
        self.feed = {DISK["id"]: DISK}
        self.cfg["incidents"]["grace_minutes"] = "0"
        self.answers = [{"message": "Đề xuất: xoá /var/backups/old (40 GB). Lệnh: rm -rf ...", "status": "need_approval"}, {"message": "Đã xoá, còn 55%."}]
        self.tick()
        self.assertEqual(self.said()[-1], ("lead", "🔴 #urgent vps-01: disk /: 96% used\n🤖 @web_bot is on it. #agent"))
        self.app.drain()
        who, method, sent = self.calls[-1]
        self.assertEqual((who, sent["reply_markup"]["inline_keyboard"][0][0]), ("web", {"text": "✅ Approve", "callback_data": "ok:2"}))
        self.app.drain()
        self.assertEqual(len(self.asked), 1)  # nothing runs while it waits
        self.press("ok:2", by=EVE, agent="web")
        self.app.drain()
        self.assertEqual(len(self.asked), 1)
        self.press("ok:2", agent="web")
        self.assertIn("✅ approved by Anna", self.said("editMessageText")[-1][1])
        self.app.drain()
        self.assertEqual(self.asked[1], ("web", "Admin Anna approved your proposal. Do exactly what you proposed, nothing more, check the result, then report.", "S1"))
        self.assertEqual((self.said()[-1], self.app.st["incidents"][DISK["id"]]["status"]), (("web", "Đã xoá, còn 55%."), "done"))
        self.press("ok:2", agent="web")  # a second press finds nothing to approve
        self.app.drain()
        self.assertEqual(len(self.asked), 2)

    def test_no_answer_is_a_no_and_a_refusal_leaves_it_to_the_admins(self):
        self.feed = {DISK["id"]: DISK, "x": dict(DISK, id="x", host="vps-02")}
        self.cfg["incidents"]["grace_minutes"] = "0"
        self.answers = [{"message": "A?", "status": "need_approval"}, {"message": "B?", "status": "need_approval"}]
        self.tick()
        self.app.drain()
        self.press("no:%s" % sorted(self.app.st["pending"])[0], agent="web")
        self.assertIn("❌ refused by Anna", self.said("editMessageText")[-1][1])
        self.tick(31 * 60)
        self.assertIn("⌛ No answer in 31m: not done.", self.said("editMessageText")[-1][1])
        self.assertIn('<a href="tg://user?id=111">admin</a>', self.said()[-1][1])
        self.assertEqual((self.app.st["pending"], sorted(i["status"] for i in self.app.st["incidents"].values())), ({}, ["left", "left"]))
        self.app.drain()
        self.assertEqual(len(self.asked), 2)

    def test_a_fix_that_did_not_hold_calls_the_admins_once(self):
        self.feed = {DISK["id"]: DISK}
        self.cfg["incidents"]["grace_minutes"] = "0"
        self.answers = [{"message": "fixed"}]
        self.tick()
        self.app.drain()
        self.tick(16 * 60)
        self.assertEqual(self.said()[-1], ("lead", 'Still open 16m after web reported done: vps-01: disk /: 96% used\nThis needs a person. #agent\n<a href="tg://user?id=111">admin</a>'.join(["⚠️ ", ""])))
        count = len(self.calls)
        self.tick(3600)
        self.assertEqual((len(self.calls), len(self.asked)), (count, 1))

    def test_an_alert_storm_is_not_answered_with_a_storm_of_agents(self):
        self.cfg["incidents"]["grace_minutes"] = "0"
        self.feed = {str(n): dict(DISK, id=str(n)) for n in range(9)}
        self.answers = [{"message": "ok"}] * 9
        self.tick()
        self.app.drain()
        self.assertEqual(len(self.asked), 6)
        self.assertEqual(sorted(i["status"] for i in self.app.st["incidents"].values()), ["done"] * 6 + ["left"] * 3)

    def test_an_agent_that_cannot_run_says_so_and_calls_the_admins(self):
        self.feed = {DISK["id"]: DISK}
        self.cfg["incidents"]["grace_minutes"] = "0"
        self.answers = [{"error": "Failed to authenticate"}]
        self.tick()
        self.app.drain()
        self.assertEqual(self.said()[-1][1], '⚠️ I could not work on this: Failed to authenticate #agent\n<a href="tg://user?id=111">admin</a>')
        self.assertEqual(self.app.st["incidents"][DISK["id"]]["status"], "left")


class Orders(Case):
    def test_only_admins_only_when_addressed_and_each_agent_answers_for_itself(self):
        self.message("@web_bot restart nginx", by=EVE)
        self.message("nginx trông chậm quá")  # not addressed to anyone: context, not an order
        self.message("@web_bot2 hello")  # another bot's name
        self.app.drain()
        self.assertEqual(self.asked, [])
        self.answers = [{"message": "nginx đang chạy, 3 worker."}]
        asked = self.message("@Web_Bot kiểm tra nginx trên vps-01")
        self.app.drain()
        who, prompt, session = self.asked[0]
        self.assertEqual((who, session), ("web", None))
        self.assertIn("Admin Anna wrote to you in the chat:\n@Web_Bot kiểm tra nginx trên vps-01", prompt)
        self.assertIn("Anna (admin): nginx trông chậm quá", prompt)
        self.assertNotIn("Eve", prompt)
        self.assertEqual((self.calls[-1][0], self.calls[-1][2]["text"], self.calls[-1][2]["reply_to_message_id"]), ("web", "nginx đang chạy, 3 worker.", asked))

    def test_a_reply_continues_the_same_session(self):
        self.answers = [{"message": "Cần restart không?", "status": "waiting"}, {"message": "Đã restart."}]
        self.message("@web_bot nginx lỗi 502")
        self.app.drain()
        sent = self.calls[-1][2]
        self.message("ừ restart đi", reply_to=("web", 5000 + len(self.calls)))
        self.app.drain()
        self.assertEqual(self.asked[1], ("web", "Admin Anna replied to your message:\nừ restart đi", "S1"))

    def test_words_instead_of_the_button_cancel_the_proposal_and_become_the_order(self):
        self.answers = [{"message": "Đề xuất: reboot", "status": "need_approval"}, {"message": "ok, chỉ restart docker"}]
        self.message("@web_bot máy treo")
        self.app.drain()
        self.assertEqual(len(self.app.st["pending"]), 1)
        self.message("đừng reboot, restart docker thôi", reply_to=("web", 5000 + len(self.calls)))
        self.assertEqual(self.app.st["pending"], {})
        self.app.drain()
        self.assertEqual(self.asked[1][2], "S1")

    def test_the_lead_hands_work_to_the_right_agent_and_handing_on_stops(self):
        self.answers = [{"message": "Kiểm tra chứng chỉ trên vps-02", "assign": "web"}, {"message": "x", "assign": "lead"}, {"message": "y", "assign": "web"}, {"message": "Tôi tự làm."}]
        self.message("@lead_bot chứng chỉ sắp hết hạn?")
        self.app.drain()
        self.assertEqual([who for who, _, _ in self.asked], ["lead", "web", "lead", "lead"][:len(self.asked)])
        self.assertEqual(self.said()[0], ("lead", "🧭 → @web_bot\nKiểm tra chứng chỉ trên vps-02"))
        self.assertIn("lead (agent) handed you this, with this brief:\nKiểm tra chứng chỉ trên vps-02", self.asked[1][1])
        self.assertEqual(len(self.asked), 3)  # two hand-overs at most; the third answer is posted as it is

    def test_an_order_that_waited_out_a_restart_is_dropped_and_other_chats_are_ignored(self):
        self.now += 3600
        m = {"message_id": 1, "date": self.now - 900, "chat": {"id": CHAT}, "from": {"id": ANNA, "first_name": "Anna"}, "text": "@web_bot reboot"}
        self.app.on_update(self.app.agents["web"], {"message": m}, self.now)
        self.app.on_update(self.app.agents["web"], {"message": dict(m, date=self.now, chat={"id": 42})}, self.now)
        self.app.on_update(self.app.agents["web"], {"message": dict(m, date=self.now, **{"from": {"id": ANNA, "is_bot": True}})}, self.now)
        self.app.drain()
        self.assertEqual(self.asked, [])

    def test_state_survives_a_restart(self):
        self.feed = {DISK["id"]: DISK}
        self.tick()
        again = oa.App(self.cfg, self.app.agents, self.app.secrets, self.app.state_path)
        self.assertEqual(again.st["incidents"][DISK["id"]]["status"], "waiting")


FAKE_CLAUDE = r'''
import json, sys
args, prompt = sys.argv[1:], sys.stdin.read()
json.dump({"seen": args}, open(sys.argv[0] + ".args", "w"))
if "FAIL" in prompt:
    print(json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth session expired", "session_id": "s-err"}))
elif "PLAIN" in prompt:
    print(json.dumps({"type": "result", "is_error": False, "result": "just text", "session_id": "s-plain"}))
elif "GARBAGE" in prompt:
    sys.stderr.write("boom\n"); sys.exit(3)
else:
    print(json.dumps({"type": "result", "is_error": False, "result": "", "session_id": "s-1", "total_cost_usd": 0.02,
                      "structured_output": {"message": "got: " + prompt, "status": "need_approval", "assign": "web"}}))
'''


class RealSubprocess(Case):
    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.dir.name, "fake_claude.py")
        with open(self.fake, "w", encoding="utf-8") as f:
            f.write(FAKE_CLAUDE)
        self.cfg["claude"]["command"] = '"%s" "%s"' % (sys.executable, self.fake)
        self.cfg["claude"]["model"] = "haiku"
        self.run_claude = lambda prompt, session=None: oa.App.claude(self.app, self.app.agents["web"], prompt, session)

    def test_command_line_and_answers(self):
        self.app.secrets["CLAUDE_CODE_OAUTH_TOKEN"] = "oauth-secret-123"
        self.assertEqual(self.run_claude("xin chào", "s-0"), {"message": "got: xin chào", "status": "need_approval", "assign": "web", "session": "s-1", "cost": 0.02})
        with open(self.fake + ".args") as f:
            args = json.load(f)["seen"]
        self.assertEqual(args[:5], ["-p", "--output-format", "json", "--json-schema", oa.SCHEMA])
        self.assertEqual(args[args.index("--allowedTools") + 1:], ["Bash(ssh *)", "Bash(curl *)", "Read", "Grep", "Glob"])
        self.assertEqual((args[args.index("--resume") + 1], args[args.index("--model") + 1]), ("s-0", "haiku"))
        self.assertIn('You are "web"', args[args.index("--append-system-prompt") + 1])
        self.assertNotIn("oauth-secret-123", " ".join(args))  # it travels in the environment, never on the command line
        self.assertEqual(self.run_claude("PLAIN"), {"message": "just text", "status": "done", "assign": "", "session": "s-plain", "cost": None})
        self.assertEqual(self.run_claude("FAIL"), {"error": "Failed to authenticate: OAuth session expired", "session": "s-err"})
        self.assertEqual(self.run_claude("GARBAGE"), {"error": "boom"})
        self.cfg["claude"]["command"] = "no-such-claude-binary"
        self.assertIn("cannot start no-such-claude-binary", self.run_claude("x")["error"])


class Daemon(unittest.TestCase):
    """The real thing end to end: threads, long polling and HTTP against a local server that plays Telegram and the
    Worker, with the stand-in for `claude -p`."""

    def test_incident_approval_by_button_and_report(self):
        import http.server
        import threading
        import time
        got, updates, lock = [], {"1:lead": [], "2:web": []}, threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                ok = self.headers.get("Authorization") == "Bearer agentsecret"
                self.answer({"now": 0, "incidents": [DISK]} if ok else {}, 200 if ok else 401)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                _, bot, method = self.path.split("/")
                token = bot[3:]
                with lock:
                    if method == "getUpdates":
                        mine, updates[token] = [u for u in updates[token] if u["update_id"] >= data.get("offset", 0)], []
                    else:
                        got.append((token, method, data))
                        n = len(got)
                if method == "getUpdates":
                    if not mine:
                        time.sleep(0.05)
                    return self.answer({"ok": True, "result": mine})
                if method == "getMe":
                    return self.answer({"ok": True, "result": {"id": int(token[0]), "username": token[2:] + "_bot"}})
                self.answer({"ok": True, "result": {"message_id": 700 + n}})

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_port
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fake = os.path.join(d.name, "fake_claude.py")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(FAKE_CLAUDE.replace('"got: " + prompt', '"Đề xuất: dọn log" if "incident" in prompt else "Đã dọn, còn 60%"').replace(
                '"status": "need_approval", "assign": "web"', '"status": "need_approval" if "incident" in prompt else "done"'))
        with open(os.path.join(d.name, "agents.ini"), "w", encoding="utf-8") as f:
            f.write(CONFIG.replace("https://w.example", base).replace("grace_minutes = 5", "grace_minutes = 0\npoll_seconds = 0.1") +
                    '[chat]\napi_host = %s\n[claude]\ncommand = "%s" "%s"\n' % (base, sys.executable, fake))
        with open(os.path.join(d.name, "agents.env"), "w", encoding="utf-8") as f:
            f.write("LEAD_BOT_TOKEN=1:lead\nWEB_BOT_TOKEN=2:web\nAGENT_TOKEN=agentsecret\n")
        cfg, agents, secrets, _, _ = oa.load_config(os.path.join(d.name, "agents.ini"))
        app = oa.App(cfg, agents, secrets, os.path.join(d.name, "state.json"))
        app.log = lambda **what: None
        self.assertEqual(app.greet(), [])
        self.assertEqual(agents["web"]["me"], {"id": 2, "username": "web_bot"})
        threading.Thread(target=app.serve, daemon=True).start()
        self.addCleanup(app.stop.set)

        def wait(what, seconds=30):
            end = time.time() + seconds
            while time.time() < end:
                with lock:
                    found = [x for x in got if what(x)]
                if found:
                    return found[-1]
                time.sleep(0.05)
            self.fail("never happened; got %r" % [(t, m, x.get("text")) for t, m, x in got])

        token, _, proposal = wait(lambda x: x[1] == "sendMessage" and x[2]["text"] == "Đề xuất: dọn log")
        self.assertEqual((token, proposal["chat_id"], proposal["reply_markup"]["inline_keyboard"][0][0]["text"]), ("2:web", -1001, "✅ Approve"))
        data = proposal["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        with lock:
            updates["2:web"].append({"update_id": 10, "callback_query": {"id": "q1", "from": {"id": EVE, "first_name": "Eve"}, "data": data}})
        wait(lambda x: x[1] == "answerCallbackQuery" and x[2].get("show_alert"))
        with lock:
            updates["2:web"].append({"update_id": 11, "callback_query": {"id": "q2", "from": {"id": ANNA, "first_name": "Anna"}, "data": data}})
        token, _, report = wait(lambda x: x[1] == "sendMessage" and x[2]["text"] == "Đã dọn, còn 60%")
        self.assertEqual(token, "2:web")
        with lock:
            texts = [x[2].get("text") for x in got if x[1] in ("sendMessage", "editMessageText")]
        self.assertEqual(texts[0], "🔴 #urgent vps-01: disk /: 96% used\n🤖 @web_bot is on it. #agent")
        self.assertIn("Đề xuất: dọn log\n\n✅ approved by Anna", texts)
        time.sleep(1.5)  # the next tick writes the state
        with open(os.path.join(d.name, "state.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["incidents"][DISK["id"]]["status"], "done")


if __name__ == "__main__":
    unittest.main()
