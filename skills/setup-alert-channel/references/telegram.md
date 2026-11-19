# Telegram: bot, chat, chat id

Contents: [1. Create the bot](#1-create-the-bot) · [2. Choose where alerts go](#2-choose-where-alerts-go) ·
[3. Find the chat id](#3-find-the-chat-id) · [4. Topics](#4-topics-optional) · [5. Limits](#5-limits-worth-knowing) ·
[6. Troubleshooting](#6-troubleshooting)

Everything here is done by the user in the Telegram app (phone or desktop). It takes about five minutes.

## 1. Create the bot

1. In Telegram, search for **@BotFather** (blue check mark) and open the chat. Press **Start**.
2. Send `/newbot`.
3. BotFather asks for a **name**: what people see, e.g. `Acme Alerts`.
4. Then a **username**: must be unique and end in `bot`, e.g. `acme_alerts_bot`.
5. BotFather answers with the **token**, a string like `1234567890:AAH…`. That is the bot's password.
   Put it after `TG_BOT_TOKEN=` in `~/.infra-alerts/notify.env`.

If the token ever leaks: BotFather → `/revoke` → pick the bot. The old token stops working at once; put the new one
in the file and on every server (`/etc/infrawatch/notify.env`) and Worker (`wrangler secret put TG_BOT_TOKEN`).

Optional, in BotFather: `/setuserpic` (an avatar makes alerts easy to spot), `/setdescription`.

## 2. Choose where alerts go

| Where | Good for | How |
|---|---|---|
| **Private chat** with the bot | One person | Open the bot (`t.me/<username>`) and press **Start**. A bot cannot write to someone who never pressed Start. |
| **Group** | A small team that also discusses alerts | New Group → add the bot as a member → send any message that mentions the bot or `/start@<username>`. |
| **Channel** (private) | A team that only reads; alerts never get buried in talk | New Channel → Private → open the channel's settings → Administrators → Add Admin → the bot, with **Post Messages** on → post any message in the channel. |
| **Group with topics** | Many machines or severities, one thread each | A group → Edit → Topics on (it becomes a supergroup) → create a topic per machine or per severity. See step 4. |

Recommendation: a **private channel** for a team, a private chat for one person. Make sure notifications are on for
it, and consider a different notification sound: an alert chat that is muted is not an alert chat.

## 3. Find the chat id

The bot only learns about a chat after something happened there (a message, the bot being added, a post in the
channel). After that:

```bash
. ~/.infra-alerts/notify.env
curl -sS "https://api.telegram.org/bot${TG_BOT_TOKEN}/getUpdates"
```

Look for `"chat":{"id":…,"title":…,"type":…}`:

- private chat: a positive number (the person's user id)
- group: a negative number
- supergroup or channel: a negative number starting with `-100`

Put it after `TG_CHAT_ID=`. On a server where infrawatch is installed, `python3 /opt/infrawatch/infrawatch.py chats`
prints the same as a short list.

A **public** channel can also be addressed by name: `TG_CHAT_ID=@channelusername`.

## 4. Topics (optional)

In a group with topics, each topic has a thread id. Post a message in the topic, run `getUpdates` again, and read
`message_thread_id`; or copy a message link from the topic (`https://t.me/c/<chat>/<topic>/<message>`): the middle
number is the topic. Put it after `TG_THREAD_ID=`. A common layout: one topic per machine (each server's
`notify.env` has its own `TG_THREAD_ID`), plus one for the Cloudflare Worker.

## 5. Limits worth knowing

- A message is at most **4096 characters**; the monitors cut longer ones.
- About **20 messages a minute to one group**, 1 a second to one chat, 30 a second in all; past that Telegram
  answers 429 with a `retry_after`. The monitors send one message per run with everything that changed, which stays
  far below this.
- A bot sees only its own chats' updates, and `getUpdates` keeps them for 24 hours.

Source: <https://core.telegram.org/bots/faq>, <https://core.telegram.org/bots/api>.

## 6. Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| `getUpdates` answers `"result":[]` | Nothing happened in the chat since the bot joined, or it was over 24 h ago | Post a message (in a group: `/start@<username>`, since bots do not see ordinary group messages by default), run it again |
| `401 Unauthorized` | Wrong or revoked token | Copy it again from BotFather (`/mybots` → the bot → API Token) |
| `409 Conflict` on `getUpdates` | The bot has a webhook set by another program | Create a separate bot just for alerts (a bot is free and takes a minute); do not remove a webhook another program relies on |
| `400 chat not found` | Wrong chat id, or the bot is not in that chat | Check the sign and the `-100` prefix; add the bot to the chat |
| `403 bot was blocked by the user` / `bot can't initiate conversation` | Private chat where the person never pressed Start, or blocked the bot | Press Start in the bot's chat |
| `403 … not enough rights to send` / `need administrator rights in the channel chat` | The bot is not an administrator of the channel, or lacks Post Messages | Channel settings → Administrators → the bot → Post Messages |
| `400 group chat was upgraded to a supergroup chat` | Turning on topics, public links or other features turned the group into a supergroup, which has a **new chat id** (starting `-100`) | Use the `migrate_to_chat_id` in the error, or run `getUpdates` again, and update `TG_CHAT_ID` everywhere. Everything keeps failing until then |
| `400 message thread not found` | `TG_THREAD_ID` is not a topic of this chat | Read it again as in step 4 |
| Messages arrive without sound | Recoveries and daily reports are sent silently on purpose; new problems are not | Nothing to do; check the chat's own mute setting if problems are silent too |
| Telegram is not reachable from the server | Network blocks it | Set `TG_API_HOST` to a reverse proxy of `api.telegram.org` you control, or use another channel ([other-channels.md](other-channels.md)) |
