"""zinxAssistant — a Discord announcement bot.

Posts scheduled @everyone embed announcements (Daily Reset, Bear Trap,
and any custom reminders you add) into a server's announcements channel.
All schedule times are UTC ("game time").

Run:  python bot.py
Setup: see README.md
"""

import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from storage import Storage

load_dotenv()


class ZinxAssistant(commands.Bot):
    def __init__(self):
        # No privileged intents needed: the bot only sends messages and
        # responds to slash commands.
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.storage = Storage()

    async def setup_hook(self):
        await self.load_extension("cogs.announcements")
        await self.tree.sync()

    async def on_ready(self):
        print(f"zinxAssistant is online as {self.user} (id: {self.user.id})")
        print("Slash commands synced. Run /setup in your server to begin.")


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and paste your bot token."
        )
    ZinxAssistant().run(token)
