# Channels other than Telegram

Contents: [Which one](#which-one) · [WeCom](#wecom--企业微信-group-bot) · [Feishu / Lark](#feishu--lark-custom-bot) ·
[DingTalk](#dingtalk--钉钉-group-bot) · [Bark](#bark-ios) · [ntfy](#ntfy) · [ServerChan](#serverchan--server酱) ·
[PushPlus](#pushplus) · [Generic webhook](#generic-webhook) · [Telegram behind a proxy](#telegram-behind-a-proxy)

Each channel is one or two keys in `~/.infra-alerts/notify.env` (and later `/etc/infrawatch/notify.env` on each
server). The key names follow the 青龙 (Qinglong) `sendNotify` convention, so an existing config can be pasted in.
Every channel that has its key set receives every message. Messages are plain text; the body is cut to about 1900
bytes so it fits the tightest limit (WeCom).

The host agent (`infrawatch.py`) speaks all of these. The Cloudflare Worker speaks Telegram, WeCom, Feishu/Lark,
DingTalk and the generic webhook.

## Which one

| You are | Use |
|---|---|
| A team on WeCom, Feishu or DingTalk | That app's group bot: nothing to install, everyone already has it |
| One person on iPhone | Bark |
| One person on Android, or self-hosting | ntfy |
| One person who wants it in WeChat | ServerChan or PushPlus (both push through a WeChat service account) |
| Anything else (Slack, Discord, a pager) | Generic webhook, into a small relay you own |

Test any of them on a server with `python3 /opt/infrawatch/infrawatch.py test`: it prints `ok` or the service's own
error for each channel.

## WeCom / 企业微信 group bot

Group → ⋯ → 添加群机器人 → 新创建一个机器人 → copy the webhook URL. The key is the part after `key=`.

```
QYWX_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Sends `POST https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>` with `{"msgtype":"text","text":{"content":…}}`.
Limits: 20 messages a minute per bot; text at most 2048 bytes. No signing.
Docs: <https://developer.work.weixin.qq.com/document/path/91770>

## Feishu / Lark custom bot

Group → 设置 → 群机器人 → 添加机器人 → 自定义机器人 → copy the webhook URL. The key is the last part of the path.
Security options: keywords (use `infra`, which every message from the host agent and the Worker contains), or 签名校验 (copy the secret).

```
FSKEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
FSSECRET=           # only if signature verification is on
FS_HOST=https://open.larksuite.com   # only for Lark (international); default is https://open.feishu.cn
```

Sends `POST <host>/open-apis/bot/v2/hook/<key>` with `{"msg_type":"text","content":{"text":…}}`, plus `timestamp`
(seconds) and `sign` in the body when a secret is set. Limits: 100 a minute and 5 a second; body 20 KB.
Docs: <https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot>

## DingTalk / 钉钉 group bot

Group → 群设置 → 机器人 → 添加机器人 → 自定义 → choose a security setting → copy the webhook URL. The token is the
part after `access_token=`. Security: 自定义关键词 (use `infra`), or 加签 (copy the secret starting `SEC`), or
an IP allow-list.

```
DD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DD_BOT_SECRET=SEC…   # only if 加签 is on
```

Sends `POST https://oapi.dingtalk.com/robot/send?access_token=<token>` (with `&timestamp=<ms>&sign=<sig>` when a
secret is set) and `{"msgtype":"text","text":{"content":…}}`. An error such as `keywords not in content` means the
keyword in the bot's settings does not appear in the message: set it to `infra`. The per-minute limit is commonly given as 20 messages; we could
not confirm it in the official docs. Docs: open.dingtalk.com → 自定义机器人接入.

## Bark (iOS)

Install Bark from the App Store; the app shows a URL like `https://api.day.app/<key>`.

```
BARK_PUSH=https://api.day.app/<key>      # or only the key; or your own server's URL with the key
```

Sends `POST <server>/push` with `device_key`, `title`, `body`, `level` (`timeSensitive` for new problems, `passive`
for recoveries and reports). Docs: <https://bark.day.app>

## ntfy

Pick a long, unguessable topic name (on ntfy.sh a topic is public to anyone who knows its name) and subscribe to it
in the ntfy app.

```
NTFY_TOPIC=acme-alerts-7f3k2q9x
NTFY_URL=https://ntfy.example.com     # only if self-hosted; default https://ntfy.sh
NTFY_TOKEN=tk_…                       # only if the server needs one
```

Sends JSON `{"topic","title","message","priority"}` to the server root. Limits: 4096 bytes a message; ntfy.sh
allows 250 messages a day. Docs: <https://docs.ntfy.sh/publish/>

## ServerChan / Server酱

Sign in at <https://sct.ftqq.com> (Turbo) or get a Server酱3 key, and copy the SendKey.

```
PUSH_KEY=SCT…        # Turbo: sends to https://sctapi.ftqq.com/<key>.send
PUSH_KEY=sctp123t…   # Server酱3: sends to https://<uid>.push.ft07.com/send/<key>.send
```

Form fields `title` and `desp`. The free tier has a small daily quota (check the current number on the site): fine for one server, not for many.

## PushPlus

<https://www.pushplus.plus>: sign in, complete real-name verification (required before any message is delivered),
copy the token.

```
PUSH_PLUS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Sends `POST https://www.pushplus.plus/send` with `token`, `title`, `content`. Limits: 200 WeChat messages a day, 5
requests a minute, and at most 3 identical messages an hour; an HTTP 200 means accepted, not delivered.
Docs: <https://www.pushplus.plus/doc/guide/api.html>

## Generic webhook

```
WEBHOOK_URL=https://relay.example.com/alerts
```

Receives `POST` JSON `{"title": "...", "body": "...", "level": "alert" | "info", "source": "infrawatch"}`; any 2xx
answer counts as taken. Slack and Discord webhooks expect their own shapes (`{"text": …}` / `{"content": …}`), so
put a few lines of relay in between (a Cloudflare Worker is enough).

## Telegram behind a proxy

Where `api.telegram.org` is not reachable from the server, point the monitors at a reverse proxy of it that you
control:

```
TG_API_HOST=https://tg-proxy.example.com
```

The proxy sees the bot token in every request path, so it must be yours. The Cloudflare Worker itself reaches
Telegram directly from Cloudflare's network, so alerts from the Worker need no proxy even when the servers do.
