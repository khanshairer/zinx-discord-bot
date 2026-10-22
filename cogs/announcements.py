
"""Announcement commands and the scheduler loop for zinxAssistant.
author : sharier khan
All times are UTC ("game time"). A background loop checks every 30
seconds whether any reminder is due and posts its embed to the server's
announcements channel.
"""

from datetime import datetime, timezone, timedelta

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


def repeated_fire_time(reminder, now):
    """Return due UTC fire time for a repeating reminder, or None."""
    anchor_day = datetime.strptime(reminder["repeat_start_date"], "%Y-%m-%d").date()
    interval = reminder["repeat_days"]
    # Check surrounding event days because lead time can cross midnight.
    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        if day < anchor_day or (day - anchor_day).days % interval:
            continue
        event_at = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        event_at = event_at.replace(hour=reminder["hour"], minute=reminder["minute"])
        fire_at = event_at - timedelta(minutes=reminder.get("lead_minutes", 0))
        if fire_at <= now < fire_at + timedelta(minutes=1):
            # Keep a separate persisted stamp to avoid duplicate posts.
            if reminder.get("last_fired") != fire_at.isoformat():
                return fire_at
    return None


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
            for r in g["reminders"]:
                if not r.get("enabled", True):
                    continue
                # Per-reminder channel overrides the channel chosen by /setup.
                # Reminders without an override still use the default channel.
                channel_id = r.get("channel_id") or g.get("channel_id")
                if not channel_id:
                    continue
                channel = self.bot.get_channel(int(channel_id))
                if channel is None:
                    continue
                if r.get("repeat_days"):
                    fire = repeated_fire_time(r, now)
                    if fire is None:
                        continue
                elif r.get("date"):
                    # A dated reminder fires once at its UTC date/time.
                    event_time = datetime.strptime(
                        r["date"], "%Y-%m-%d"
                    ).replace(
                        hour=r["hour"],
                        minute=r["minute"],
                        tzinfo=timezone.utc,
                    )
                    fire = event_time - timedelta(
                        minutes=r.get("lead_minutes", 0)
                    )
                    # Allow one minute for the 30-second polling loop.
                    if not (fire <= now < fire + timedelta(minutes=1)) or r.get("last_fired") == fire.isoformat():
                        continue
                else:
                    fire = due_fire_time(r, now)
                    if fire is None:
                        continue
                try:
                    await self.post_reminder(channel, r)
                except discord.HTTPException:
                    continue
                self.storage.mark_fired(gid, r["id"], fire.isoformat())
                if r.get("date") and not r.get("repeat_days"):
                    self._update_reminder(gid, str(r["id"]), {"enabled": False})

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

        persist = self.storage.save
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
            "Default reminder: Daily Reset (00:00 UTC). "
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

    @app_commands.command(name="addreminder", description="Add an announcement: once or every X days (UTC)")
    @app_commands.describe(
        name="Short name, e.g. Bear Trap",
        time="Event time in UTC, HH:MM — e.g. 19:00",
        title="Embed title (supports {time}, {lead}, {name})",
        body="Embed text (supports {time}, {lead}, {name})",
        emoji="Emoji shown before the title",
        lead_minutes="Post this many minutes BEFORE the event time (default 0)",
        ping="Ping @everyone (default: yes)",
        repeat="Repeat every X days (0=once, 1=daily, 3=every 3 days)",
        date="Event/start date YYYY-MM-DD UTC (default: today UTC)",
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
        repeat: int = 0,
        date: str = None,
    ):
        try:
            hour, minute = parse_time(time)
        except ValueError as exc:
            await interaction.response.send_message(f"Bad time: {exc}", ephemeral=True)
            return
        if not 0 <= lead_minutes <= 720:
            await interaction.response.send_message(
                "lead_minutes must be between 0 and 720.", ephemeral=True
            )
            return
        if not 0 <= repeat <= 3650:
            await interaction.response.send_message(
                "repeat must be between 0 (once) and 3650 days.", ephemeral=True
            )
            return

        # Date is the event date (and repeat anchor), in UTC.
        # Without a supplied date, use TODAY rather than silently choosing tomorrow.
        now = datetime.now(timezone.utc)
        if date is None:
            event_day = now.date()
        else:
            try:
                event_day = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                await interaction.response.send_message(
                    "❌ Invalid date. Use YYYY-MM-DD (UTC).", ephemeral=True
                )
                return

        event_at = datetime.combine(
            event_day, datetime.min.time(), tzinfo=timezone.utc
        ).replace(hour=hour, minute=minute)
        first_fire = event_at - timedelta(minutes=lead_minutes)
        if first_fire <= now:
            await interaction.response.send_message(
                "❌ The announcement time for that date has already passed (UTC). "
                "Choose a future date/time, accounting for lead_minutes.",
                ephemeral=True,
            )
            return
        first_day = event_day.isoformat()

        reminder = self.storage.add_reminder(
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
                "date": first_day if repeat == 0 else None,
                "repeat_days": repeat if repeat > 0 else None,
                "repeat_start_date": first_day if repeat > 0 else None,
            },
        )
        schedule = ("once" if repeat == 0 else
                    "daily" if repeat == 1 else f"every {repeat} days")
        await interaction.response.send_message(
            f"✅ Reminder added: **{reminder['name']}** at "
            f"**{hour:02d}:{minute:02d} UTC**.\n"
            f"Schedule: **{schedule}**, first event: **{first_day}** UTC.\n"
            f"Lead: **{lead_minutes} min** · ID: `{reminder['id']}`",
            ephemeral=True,
        )

    @app_commands.command(name="setdate", description="Set a one-time UTC date for a reminder")
    @app_commands.describe(
        reminder_id="ID from /reminders",
        date="Event date in YYYY-MM-DD format, e.g. 2026-10-25",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setdate(self, interaction: discord.Interaction, reminder_id: str, date: str):
        try:
            event_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date. Use YYYY-MM-DD.", ephemeral=True
            )
            return

        reminder_id = reminder_id.strip()
        guild = self.storage.get_guild(interaction.guild_id)
        reminder = next(
            (r for r in guild["reminders"] if str(r["id"]) == reminder_id),
            None,
        )
        if reminder is None:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )
            return

        event_time = datetime.combine(
            event_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).replace(hour=reminder["hour"], minute=reminder["minute"])
        fire_time = event_time - timedelta(
            minutes=reminder.get("lead_minutes", 0)
        )
        if fire_time <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ That reminder's announcement time is already in the past (UTC).",
                ephemeral=True,
            )
            return

        self._update_reminder(
            interaction.guild_id,
            reminder_id,
            {"date": event_date.isoformat(), "enabled": True,
             "repeat_days": None, "repeat_start_date": None,
             "repeat_last_fire": None},
        )
        await interaction.response.send_message(
            f"📅 **Reminder date updated!**\n"
            f"**Reminder:** {reminder['name']}\n"
            f"**Event date:** {event_date.isoformat()}\n"
            f"**Event time:** {reminder['hour']:02d}:{reminder['minute']:02d} UTC\n"
            f"**Announcement time:** <t:{int(fire_time.timestamp())}:F>\n"
            "This reminder will post once and then pause.",
            ephemeral=True,
        )

    @app_commands.command(name="repeat", description="Repeat a reminder every X days (UTC)")
    @app_commands.describe(
        reminder_id="Reminder ID shown by /reminders",
        days="Repeat interval in days (1=daily, 2=every 2 days)",
        start_date="Optional first event date YYYY-MM-DD (UTC); defaults to today",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def repeat(
        self,
        interaction: discord.Interaction,
        reminder_id: str,
        days: int,
        start_date: str = None,
    ):
        if not 1 <= days <= 3650:
            await interaction.response.send_message(
                "Days must be between 1 and 3650.", ephemeral=True
            )
            return
        guild = self.storage.get_guild(interaction.guild_id)
        reminder_id = reminder_id.strip()
        reminder = next(
            (r for r in guild["reminders"] if str(r["id"]) == reminder_id), None
        )
        if reminder is None:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )
            return
        if start_date:
            try:
                anchor_day = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                await interaction.response.send_message(
                    "Use YYYY-MM-DD for start_date.", ephemeral=True
                )
                return
        else:
            # Preserve the existing event date when converting a one-time reminder.
            anchor_day = (datetime.strptime(reminder["repeat_start_date"], "%Y-%m-%d").date()
                          if reminder.get("repeat_start_date") and reminder.get("repeat_days")
                          else datetime.strptime(reminder["date"], "%Y-%m-%d").date()
                          if reminder.get("date") else datetime.now(timezone.utc).date())

        self._update_reminder(interaction.guild_id, reminder_id, {
            "repeat_days": days,
            "repeat_start_date": anchor_day.isoformat(),
            "repeat_last_fire": None,
            "date": None,
            "enabled": True,
        })
        await interaction.response.send_message(
            f"🔁 **{reminder['name']}** will repeat every **{days}** day(s) "
            f"starting **{anchor_day.isoformat()}** at "
            f"**{reminder['hour']:02d}:{reminder['minute']:02d} UTC** "
            f"(lead {reminder.get('lead_minutes', 0)} min).",
            ephemeral=True,
        )

    @app_commands.command(name="setchannel", description="Set a channel for one reminder, or reset it to /setup default")
    @app_commands.describe(
        reminder_id="ID shown by /reminders",
        channel="Where to post this reminder (omit to use /setup channel)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setchannel(
        self,
        interaction: discord.Interaction,
        reminder_id: str,
        channel: discord.TextChannel = None,
    ):
        reminder_id = reminder_id.strip()
        guild = self.storage.get_guild(interaction.guild_id)
        reminder = next(
            (r for r in guild["reminders"] if str(r["id"]) == reminder_id), None
        )
        if reminder is None:
            await interaction.response.send_message(
                f"No reminder with id `{reminder_id}`.", ephemeral=True
            )
            return

        if channel is not None:
            me = interaction.guild.me
            permissions = channel.permissions_for(me)
            if not (permissions.view_channel and permissions.send_messages
                    and permissions.embed_links):
                await interaction.response.send_message(
                    "I need View Channel, Send Messages and Embed Links permissions "
                    f"in {channel.mention}.", ephemeral=True
                )
                return
            if reminder.get("ping", True) and not permissions.mention_everyone:
                await interaction.response.send_message(
                    f"I also need Mention Everyone permission in {channel.mention} "
                    "for this reminder's @everyone ping.", ephemeral=True
                )
                return

        if channel is None and not guild.get("channel_id"):
            await interaction.response.send_message(
                "No default channel set. Run `/setup` first.", ephemeral=True
            )
            return

        # None removes the custom override; the scheduler uses /setup's channel.
        self._update_reminder(
            interaction.guild_id, reminder_id,
            {"channel_id": channel.id if channel is not None else None},
        )
        target = channel.mention if channel is not None else f"<#{guild['channel_id']}>"
        await interaction.response.send_message(
            f"✅ **{reminder['name']}** (`{reminder_id}`) will post in {target}."
            + (" (Default channel)" if channel is None else ""),
            ephemeral=True,
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
            schedule = (f"every {r['repeat_days']} day(s) from {r['repeat_start_date']}"
                        if r.get("repeat_days") else
                        (f"once on {r['date']}" if r.get("date") else "daily"))
            lines.append(
                f"`{r['id']}` {r.get('emoji', '')} **{r['name']}** — {r['hour']:02d}:{r['minute']:02d} UTC, "
                f"{schedule}, lead {r['lead_minutes']} min, ping {'yes' if r.get('ping', True) else 'no'}, "
                f"channel <#{r.get('channel_id') or g.get('channel_id')}>"
                f"{' (custom)' if r.get('channel_id') else ' (default)'}, {state}"
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
        g = self.storage.get_guild(interaction.guild_id)
        match = next((r for r in g["reminders"] if r["id"] == reminder_id.strip()), None)
        if match is None:
            await interaction.response.send_message(f"No reminder with id `{reminder_id}`.", ephemeral=True)
            return
        channel_id = match.get("channel_id") or g.get("channel_id")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            await interaction.response.send_message(
                "Reminder channel unavailable. Run `/setup` or `/setchannel`.", ephemeral=True
            )
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

    @app_commands.command(name="help", description="Learn what each zinxAssistant command does")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💜 zinxAssistant — Command Help",
            description=(
                "Schedule Whiteout Survival announcements in **UTC (game time)**.\n"
                "Run `/setup` first to choose the default channel.\n"
                "🔒 = Requires **Manage Server** permission."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="🔒 /setup channel", value="Choose the default announcement channel; adds Daily Reset on first setup.", inline=False)
        embed.add_field(name="🔒 /announce title message", value="Send an announcement immediately (with optional emoji and @everyone).", inline=False)
        embed.add_field(name="🔒 /addreminder", value="Create a reminder with date, UTC time, optional lead minutes, and repeat days. Repeat 0 = once; 1 = daily; 3 = every 3 days.", inline=False)
        embed.add_field(name="/reminders", value="List reminders, IDs, schedules, status, and their destination channels.", inline=False)
        embed.add_field(name="🔒 /setdate reminder_id date", value="Set a one-time event date in YYYY-MM-DD UTC.", inline=False)
        embed.add_field(name="🔒 /repeat reminder_id days", value="Repeat an existing reminder every X days; optional start date.", inline=False)
        embed.add_field(name="🔒 /setchannel reminder_id channel", value="Set a custom channel for one reminder; omit channel to restore `/setup` default.", inline=False)
        embed.add_field(name="🔒 /editreminder reminder_id", value="Update an existing reminder's name, time, title, text, emoji, lead time, or ping.", inline=False)
        embed.add_field(name="🔒 /testreminder reminder_id", value="Send a test immediately to that reminder's chosen channel.", inline=False)
        embed.add_field(name="🔒 /pausereminder /resumereminder", value="Temporarily stop or resume a reminder by ID.", inline=False)
        embed.add_field(name="🔒 /removereminder /deletereminder", value="Permanently delete a reminder by ID (both commands do the same thing).", inline=False)
        embed.set_footer(text="All dates and times use UTC • /help is available to everyone")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- error handling ----------

    @setup.error
    @announce.error
    @addreminder.error
    @setdate.error
    @setchannel.error
    @repeat.error
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
