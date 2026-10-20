# zinxAssistant

A Discord announcement bot for game alliances. It posts scheduled
**@everyone** embed announcements — Daily Reset, Bear Trap, and any custom
reminders you add — into your server's announcements channel, just like
the big alliance bots do.

All schedule times are **UTC** ("game time"), matching how Whiteout
Survival events run.

## What it does

- Posts a pretty embed + `@everyone` ping at each scheduled time
- Ships with two defaults (added automatically on first `/setup`):
  - 🔄 **Daily Reset** — 00:00 UTC
  - 🐻 **Bear Trap** — 19:00 UTC
- Add your own reminders with slash commands — no code changes needed
- Optional **lead time**: post X minutes *before* an event starts
- Per-server settings, stored in a local `data.json` file

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `/setup #channel` | Manage Server | Set the announcements channel (seeds the 2 defaults) |
| `/announce title message` | Manage Server | Post a one-off announcement right now |
| `/addreminder` | Manage Server | Add a daily scheduled announcement |
| `/reminders` | Anyone | List this server's reminders and their ids |
| `/removereminder id` | Manage Server | Remove a reminder |
| `/testreminder id` | Manage Server | Post a reminder immediately, as a test |

In `/addreminder`, the title and body can use placeholders:
`{time}` (the event time, e.g. `19:00`), `{lead}` (lead minutes),
`{name}` (the reminder name).

## Setup (one time, ~10 minutes)

### 1. Create the bot on Discord

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Name it **zinxAssistant** and create it.
3. Open the **Bot** tab and click **Reset Token**. Copy the token — you'll
   need it in step 3. **Never share this token with anyone.**
4. No privileged intents are needed, so leave the intent toggles off.

### 2. Invite it to your server

Replace `YOUR_APP_ID` (the Application ID from the General Information tab)
in this link, open it, and pick your server:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&permissions=150528&scope=bot%20applications.commands
```

Those permissions are only: View Channel, Send Messages, Embed Links,
Mention Everyone.

### 3. Run the bot

You need Python 3.10+ installed.

```bash
cd zinx-assistant
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then paste your token into .env
python bot.py
```

You should see `zinxAssistant is online as ...`.

### 4. Configure it in Discord

In your server, run:

```
/setup #announcements
```

That sets the channel and adds the Daily Reset + Bear Trap defaults.
Check them with `/reminders`, try `/testreminder <id>`, and add your own
events (Crazy Joe, Foundry, rallies...) with `/addreminder`.

## Keeping it online 24/7

The bot only posts while the script is running. Options:

- **Your PC**: fine for testing; announcements stop when it sleeps.
- **A cheap VPS** (e.g. Hetzner ~$5/month): run it under `systemd` or in a
  `screen`/`tmux` session so it restarts on reboot/crash.
- **A host like Railway/Render**: deploy this folder, set `DISCORD_TOKEN`
  as an environment variable there, and use `python bot.py` as the start
  command. Note: on free tiers the `data.json` file may reset on redeploy —
  a VPS is the safer home.

## Files

- `bot.py` — entry point
- `cogs/announcements.py` — slash commands + the scheduler loop
- `storage.py` — per-server settings and reminder storage (`data.json`)
- `.env` — your bot token (created from `.env.example`; never commit it)

## Troubleshooting

- **Slash commands don't appear**: the bot syncs them on startup; give it a
  minute, and make sure the invite link included the `applications.commands`
  scope.
- **Bot is online but never posts**: check `/setup` was run, the bot can
  view + send in that channel, and it has the Mention Everyone permission.
- **`DISCORD_TOKEN is not set`**: you forgot step 3's `.env` file, or the
  token has spaces around it.
