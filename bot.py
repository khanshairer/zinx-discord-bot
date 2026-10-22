"""zinxAssistant — Discord announcements and welcome bot."""

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from storage import Storage

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))


class ZinxAssistant(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.storage = Storage()

    async def setup_hook(self):
        await self.load_extension("cogs.announcements")
        await self.tree.sync()
        logger.info("Announcement extension loaded and commands synced")

    async def on_ready(self):
        logger.info(
            "Bot connected: user_id=%s, guilds=%s",
            self.user.id,
            len(self.guilds),
        )

    async def on_guild_join(self, guild: discord.Guild):
        """Explain setup after zinxAssistant is invited into a new server."""
        embed = discord.Embed(
            title="💜 zinxAssistant is here!",
            description=(
                "Thanks for adding me! I post scheduled alliance announcements "
                "and reminders in **UTC (game time)**.\n\n"
                "🔧 An admin can run `/setup` to choose the default "
                "announcement channel.\n"
                "📖 **Use `/help` for more**, including all available commands."
            ),
            color=discord.Color.from_rgb(153, 102, 255),
        )
        embed.set_footer(text="💜 zinxAssistant • Your server companion")
        # Prefer the system channel; otherwise use the first writable text channel.
        candidates = ([guild.system_channel] if guild.system_channel else []) + list(guild.text_channels)
        for channel in candidates:
            if channel is None:
                continue
            me = guild.me
            if me is None:
                continue
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                continue
            try:
                await channel.send(embed=embed)
                logger.info("Joined guild=%s; sent intro to channel=%s", guild.id, channel.id)
                return
            except discord.HTTPException:
                logger.exception("Could not send intro in channel=%s", channel.id)
        logger.warning("No writable introduction channel in guild=%s", guild.id)

    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        channel = None

        if WELCOME_CHANNEL_ID:
            channel = guild.get_channel(WELCOME_CHANNEL_ID)

        if channel is None:
            settings = self.storage.get_guild(guild.id)
            channel_id = settings.get("channel_id")

            if channel_id:
                channel = guild.get_channel(int(channel_id))

        if channel is None:
            channel = guild.system_channel

        if channel is None:
            logger.warning(
                "Welcome channel unavailable: guild_id=%s",
                guild.id,
            )
            return

        embed = discord.Embed(
            title="✨ Welcome to the Server! ✨",
            description=(
                f"Hey {member.mention}! 💜\n\n"
                f"Welcome to **{guild.name}**!\n\n"
                "🌟 We're excited to have you here!\n"
                "📜 Please read the server rules.\n"
                "💬 Introduce yourself and make friends.\n"
                "🎮 Enjoy your stay!\n"
                "📖 Type `/help` for more information about zinxAssistant!\n\n"
                f"🎉 Member #{guild.member_count}"
            ),
            color=discord.Color.from_rgb(153, 102, 255),
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.set_footer(
            text="💜 zinxAssistant • Your server companion"
        )

        if guild.banner:
            embed.set_image(url=guild.banner.url)

        try:
            await channel.send(
                content=f"🎉 Welcome {member.mention}!",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    everyone=False,
                    roles=False,
                ),
            )

            logger.info(
                "Welcome sent: guild_id=%s, member_id=%s, channel_id=%s",
                guild.id,
                member.id,
                channel.id,
            )

        except discord.Forbidden:
            logger.error(
                "Missing welcome channel permissions: guild_id=%s, channel_id=%s",
                guild.id,
                channel.id,
            )

        except discord.HTTPException:
            logger.exception(
                "Failed to send welcome: guild_id=%s, member_id=%s",
                guild.id,
                member.id,
            )


def main():
    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not configured")

    bot = ZinxAssistant()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
