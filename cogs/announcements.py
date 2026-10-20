"""Announcement commands and the scheduler loop for zinxAssistant.

All times are UTC ("game time"). A background loop checks every 30
seconds whether any reminder is due and posts its embed to the server's
announcements channel.
"""

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from storage import due_fire_time, render


def parse_time(text):
    """Parse 'HH:MM' into (hour, minute). Raises ValueError if invalid."""
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise ValueError("Use HH:MM, e.g. 19:00")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Hour must be 0-23 and minute 0-59")
    return hour, minute


def build_embed(emoji, title, body):
    heading = f"{emoji} {title}".strip() if emoji else title
    return discord.Embed(title=heading, description=body, color=discord.Color.blurple())


class Announcements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.storage = bot.storage
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()

    # ---------- scheduler ----------

    @tasks.loop(seconds=30)
    async def scheduler(self):
        now = datetime.now(timezone.utc)
        for gid, g in list(self.storage.data["guilds"].items()):
            channel_id = g.get("channel_id")
            if not channel_id:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                continue
            for r in g["reminders"]:
                if not r.get("enabled", True):
                    continue
                fire = due_fire_time(r, now)
                if fire is None:
                    continue
                try:
                    await self.post_reminder(channel, r)
                except discord.HTTPException:
                    continue
                self.storage.mark_fired(gid, r["id"], fire.isoformat())

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    async def post_reminder(self, channel, reminder):
        title, body = render(reminder)
        embed = build_embed(reminder.get("emoji", ""), title, body)
        content = "@everyone" if reminder.get("ping", True) else None
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )

    # ---------- helpers ----------

    async def _require_channel(self, interaction):
        g = self.storage.get_guild(interaction.guild_id)
        if not g.get("channel_id"):
            await interaction.response.send_message(
                "No announcements channel set yet. An admin needs to run `/setup` first.",
                ephemeral=True,
            )
            return None
        channel = self.bot.get_channel(g["channel_id"])
        if channel is None:
            await interaction.response.send_message(
                "The announcements channel no longer exists. Run `/setup` again.",
                ephemeral=True,
            )
            return None
        return channel

    def _update_reminder(self, guild_id, reminder_id, changes):
        """Update an existing reminder, keeping its ID and persisting changes."""
        guild = self.storage.get_guild(guild_id)
        reminder = next(
            (r for r in guild["reminders"] if str(r["id"]) == reminder_id),
            None,
        )
        if reminder is None:
            return False

        # Storage is shared with the existing scheduler. Mutate in place.
        previous = reminder.copy()
        reminder.update(changes)

        # Storage's persistence method wasn't included with the supplied files.
        # Support common method names without changing the existing data format.
        persist = next(
            (getattr(self.storage, name) for name in ("save", "_save", "save_data", "_save_data")
             if callable(getattr(self.storage, name, None))),
            None,
        )
        if persist is None:
            reminder.clear()
            reminder.update(previous)
            raise RuntimeError(
                "Storage has no supported save method (save/_save/save_data/_save_data). "
                "Check storage.py and update _update_reminder to call its persistence method."
            )
        try:
            persist()
        except Exception:
            reminder.clear()
            reminder.update(previous)
            raise
        return True

    # ---------- commands ----------

    @app_commands.command(name="setup", description="Set the channel zinxAssistant posts announcements in")
    @app_commands.describe(channel="The announcements channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.storage.set_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Announcements channel set to {channel.mention}. "
            "Default reminders added: Daily Reset (00:00 UTC) and Bear Trap (19:00 UTC). "
            "Use `/reminders` to see them, `/addreminder` for more."
        )

    @app_commands.command(name="announce", description="Post a one-off announcement right now")
    @app_commands.describe(title="Announcement title", message="Announcement text", emoji="Optional emoji for the title", ping="Ping @everyone (default: yes)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def announce(
        self,
        interaction: discord.Interaction,
        title: str,
        message: str,
        emoji: str = "📢",
        ping: bool = True,
    ):
        channel = await self._require_channel(interaction)
        if channel is None:
            return
        embed = build_embed(emoji, title, message)
        await channel.send(
            content="@everyone" if ping else None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        await interaction.response.send_message(f"Posted in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="addreminder", description="Add a daily scheduled announcement (times are UTC)")
    @app_commands.describe(
        name="Short name, e.g. Bear Trap",
        time="Event time in UTC, HH:MM — e.g. 19:00",
        title="Embed title (supports {time}, {lead}, {name})",
        body="Embed text (supports {time}, {lead}, {name})",
        emoji="Emoji shown before the title",
        lead_minutes="Post this many minutes BEFORE the event time (default 0)",
        ping="Ping @everyone (default: yes)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def addreminder(
        self,
        interaction: discord.Interaction,
        name: str,
        time: str,
        title: str,
        body: str,
        emoji: str = "📢",
        lead_minutes: int = 0,
        ping: bool = True,
    ):
        try:
            hour, minute = parse_time(time)
        except ValueError as e:
            await interaction.response.send_message(f"Bad time: {e}", ephemeral=True)
            return
        if lead_minutes < 0 or lead_minutes > 720:
            await interaction.response.send_message("lead_minutes must be between 0 and 720.", ephemeral=True)
            return
        r = self.storage.add_reminder(
            interaction.guild_id,
            {
                "name": name,
                "emoji": emoji,
                "hour": hour,
                "minute": minute,
                "lead_minutes": lead_minutes,
                "title": title,
                "body": body,
                "ping": ping,
                "enabled": True,
            },
        )
        await interaction.response.send_message(
            f"Reminder added: **{r['name']}** at {hour:02d}:{minute:02d} UTC "
            f"(lead {lead_minutes} min, id `{r['id']}`)."
        )

    @app_commands.command(name="reminders", description="List this server's scheduled announcements")
    async def reminders(self, interaction: discord.Interaction):
        g = self.storage.get_guild(interaction.guild_id)
        if not g["reminders"]:
            await interaction.response.send_message("No reminders yet. An admin can add one with `/addreminder`.", ephemeral=True)
            return
        lines = []
        for r in g["reminders"]:
            state = "on" if r.get("enabled", True) else "off"
            lines.append(
                f"`{r['id']}` {r.get('emoji', '')} **{r['name']}** — {r['hour']:02d}:{r['minute']:02d} UTC, "
                f"lead {r['lead_minutes']} min, ping {'yes' if r.get('ping', True) else 'no'}, {state}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="removereminder", description="Remove a scheduled announcement by its id (see /reminders)")
    @app_commands.describe(reminder_id="The id shown by /reminders")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removereminder(self, interaction: discord.Interaction, reminder_id: str):
        if self.storage.remove_reminder(interaction.guild_id, reminder_id.strip()):
            await interaction.response.send_message(f"Reminder `{reminder_id}` removed.")
        else:
            await interaction.response.send_message(f"No reminder with id `{reminder_id}`.", ephemeral=True)

    @app_commands.command(name="testreminder", description="Post a reminder right now as a test (see /reminders for ids)")
    @app_commands.describe(reminder_id="The id shown by /reminders")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def testreminder(self, interaction: discord.Interaction, reminder_id: str):
        channel = await self._require_channel(interaction)
        if channel is None:
            return
        g = self.storage.get_guild(interaction.guild_id)
        match = next((r for r in g["reminders"] if r["id"] == reminder_id.strip()), None)
        if match is None:
            await interaction.response.send_message(f"No reminder with id `{reminder_id}`.", ephemeral=True)
            return
        await self.post_reminder(channel, match)
        await interaction.response.send_message(f"Test posted in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="editreminder", description="Edit an existing reminder (UTC times)")
    @app_commands.describe(
        reminder_id="ID from /reminders",
        name="New reminder name (optional)",
        time="New event time, HH:MM UTC (optional)",
        title="New embed title (optional)",
        body="New embed text (optional)",
        emoji="New title emoji (optional)",
        lead_minutes="Minutes before the event, 0-720 (optional)",
        ping="Whether to ping @everyone (optional)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def editreminder(
        self,
        interaction: discord.Interaction,
        reminder_id: str,
        name: str = None,
        time: str = None,
        title: str = None,
        body: str = None,
        emoji: str = None,
        lead_minutes: int = None,
        ping: bool = None,
    ):
        changes = {}
        if name is not None:
            changes["name"] = name
        if time is not None:
            try:
                hour, minute = parse_time(time)
            except ValueError as exc:
                await interaction.response.send_message(f"Bad time: {exc}", ephemeral=True)
                return
            changes["hour"] = hour
            changes["minute"] = minute
        if title is not None:
            changes["title"] = title
        if body is not None:
            changes["body"] = body
        if emoji is not None:
            changes["emoji"] = emoji
        if lead_minutes is not None:
            if not 0 <= lead_minutes <= 720:
                await interaction.response.send_message(
                    "lead_minutes must be between 0 and 720.", ephemeral=True
                )
                return
            changes["lead_minutes"] = lead_minutes
        if ping is not None:
            changes["ping"] = ping
        if not changes:
            await interaction.response.send_message(
                "Provide at least one field to change.", ephemeral=True
            )
            return
        if self._update_reminder(interaction.guild_id, reminder_id.strip(), changes):
            await interaction.response.send_message(
                f"✅ Reminder `{reminder_id}` updated.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )

    @app_commands.command(name="pausereminder", description="Pause a scheduled reminder")
    @app_commands.describe(reminder_id="ID from /reminders")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pausereminder(self, interaction: discord.Interaction, reminder_id: str):
        if self._update_reminder(
            interaction.guild_id, reminder_id.strip(), {"enabled": False}
        ):
            await interaction.response.send_message(
                f"⏸️ Reminder `{reminder_id}` paused.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )

    @app_commands.command(name="resumereminder", description="Resume a paused reminder")
    @app_commands.describe(reminder_id="ID from /reminders")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def resumereminder(self, interaction: discord.Interaction, reminder_id: str):
        if self._update_reminder(
            interaction.guild_id, reminder_id.strip(), {"enabled": True}
        ):
            await interaction.response.send_message(
                f"▶️ Reminder `{reminder_id}` resumed.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )

    @app_commands.command(name="deletereminder", description="Delete a reminder by its ID")
    @app_commands.describe(reminder_id="ID from /reminders")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deletereminder(self, interaction: discord.Interaction, reminder_id: str):
        if self.storage.remove_reminder(interaction.guild_id, reminder_id.strip()):
            await interaction.response.send_message(
                f"🗑️ Reminder `{reminder_id}` deleted.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )

    # ---------- error handling ----------

    @setup.error
    @announce.error
    @addreminder.error
    @removereminder.error
    @testreminder.error
    @editreminder.error
    @pausereminder.error
    @resumereminder.error
    @deletereminder.error
    async def _permission_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the Manage Server permission to do that.", ephemeral=True
            )
        else:
            await interaction.response.send_message(f"Something went wrong: {error}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Announcements(bot))
