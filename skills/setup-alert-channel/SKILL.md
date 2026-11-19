---
name: setup-alert-channel
description: Sets up the chat that infrastructure alerts are sent to and proves it with a test message. Walks the user through creating a Telegram bot with BotFather, creating a private channel or group (optionally with topics), finding the chat id, and storing the token safely. Also covers WeCom, Feishu/Lark, DingTalk, Bark, ntfy, ServerChan, PushPlus and a generic webhook for people who cannot reach Telegram. Use when the user wants alerts or notifications on Telegram or another chat app, asks how to create a Telegram bot or channel for notifications, needs a chat id, or before monitor-hosts or monitor-cloudflare when no channel exists yet. Triggers include "telegram bot", "alert channel", "chat id", "thông báo telegram", "tạo bot telegram", "kênh nhận cảnh báo", "告警推送", "机器人 webhook", "钉钉", "飞书", "企业微信".
license: MIT
---

# Set up the alert channel

The goal: a chat that receives a test message, and a local file holding its credentials that the monitoring skills
(`monitor-hosts`, `monitor-cloudflare`) read from. Paths below are relative to this skill's folder.

## Rules for secrets

A bot token or webhook key lets anyone post as the bot. Treat it like a password.

- Keep it in one file on the user's machine: `~/.infra-alerts/notify.env`, mode 600. Never in a repository, a commit,
  a URL you print, or a message you write.
- Prefer that the user types it into the file themselves. Create the file with empty keys, ask them to open it, paste
  the value after the `=`, save, and tell you when done.
- If they paste a token into the chat anyway, write it to the file and carry on. Do not repeat it back. Mention once
  that it is now in the conversation history, and that `/revoke` in BotFather issues a new one if that matters to them.
- Commands you run must read the token from the file, and must not echo it. The examples below do this.

## Steps

1. **Pick the channel.** Default to Telegram. If the user is in mainland China or says Telegram is not reachable,
   read [references/other-channels.md](references/other-channels.md) and use WeCom, Feishu or DingTalk instead
   (or a Telegram proxy via `TG_API_HOST`). Several channels can be set at once; each gets every message.

2. **Create the file** (once):

   ```bash
   mkdir -p ~/.infra-alerts && chmod 700 ~/.infra-alerts
   [ -f ~/.infra-alerts/notify.env ] || printf 'TG_BOT_TOKEN=\nTG_CHAT_ID=\n' > ~/.infra-alerts/notify.env
   chmod 600 ~/.infra-alerts/notify.env
   ```

3. **Telegram: the user creates the bot and the chat.** These happen in the Telegram app, so guide them step by
   step from [references/telegram.md](references/telegram.md), in their language, one step at a time, waiting for
   each. In short: BotFather `/newbot` gives the token; then a private chat, a group, or a channel with the bot added
   (as administrator, for a channel); then one message posted there so the bot can see the chat.

4. **Find the chat id** once the token is in the file and a message has been posted:

   ```bash
   . ~/.infra-alerts/notify.env
   curl -sS "${TG_API_HOST:-https://api.telegram.org}/bot${TG_BOT_TOKEN}/getUpdates"
   ```

   Read `chat.id` (and `chat.title`, `chat.type`) from the answer and confirm with the user which chat is theirs.
   Groups are negative, supergroups and channels start with `-100`. Write it after `TG_CHAT_ID=` in the file. An
   empty `result`, a 401, or a 409 are explained in the reference.

5. **Send the test message** and have the user confirm they see it:

   ```bash
   . ~/.infra-alerts/notify.env
   curl -sS -o /dev/null -w '%{http_code}\n' "${TG_API_HOST:-https://api.telegram.org}/bot${TG_BOT_TOKEN}/sendMessage" \
     -d chat_id="${TG_CHAT_ID}" ${TG_THREAD_ID:+-d message_thread_id="${TG_THREAD_ID}"} \
     --data-urlencode text="✅ infra-alerts: this chat will receive alerts."
   ```

   `200` and a message in the chat means done. Anything else: drop `-o /dev/null -w …` to read Telegram's
   `description`, and see the troubleshooting table in the reference.

6. **Hand over.** Tell the user where the file is and what comes next: `monitor-hosts` copies it to each server as
   `/etc/infrawatch/notify.env`; `monitor-cloudflare` loads the same values as Worker secrets. Do not start either
   unless asked.

## What good looks like

- The user saw the test message in the chat they intend to watch, with notifications on.
- `~/.infra-alerts/notify.env` is mode 600 and holds the token and chat id; nothing else does.
- For a team: alerts go to a group or channel, not one person's private chat, so they survive that person leaving.
