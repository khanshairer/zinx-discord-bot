"""Persistent storage for zinxAssistant.

Keeps one JSON file (data.json) next to this module with per-server
(guild) settings: the announcements channel and the list of scheduled
reminders. All reminder times are UTC ("game time").
"""

import json
import os
import threading
import uuid
from datetime import timedelta

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

# Seeded the first time a server runs /setup. Times are UTC.
DEFAULT_REMINDERS = [
    {
        "name": "Daily Reset",
        "emoji": "🔄",
        "hour": 0,
        "minute": 0,
        "lead_minutes": 0,
        "title": "Daily Reset",
        "body": "Daily Reset in {lead} minutes - make sure you have done your dailies and arena battles!",
        "ping": True,
        "enabled": True,
    },
    {
        "name": "Bear Trap",
        "emoji": "🐻",
        "hour": 19,
        "minute": 0,
        "lead_minutes": 0,
        "title": "{time} Bear Trap",
        "body": "The {time} Bear Trap opens in {lead} minutes. Get your buffs on and prepare your marches for the hunt!",
        "ping": True,
        "enabled": True,
    },
]


def due_fire_time(reminder, now):
    """Return the fire datetime if this reminder should fire right now.

    A reminder fires once per day at (event time - lead_minutes) UTC.
    Returns the scheduled fire datetime when `now` is within 59 seconds
    after it and it has not already fired, otherwise None.
    """
    for day_offset in (-1, 0, 1):
        event = (now + timedelta(days=day_offset)).replace(
            hour=reminder["hour"], minute=reminder["minute"], second=0, microsecond=0
        )
        fire = event - timedelta(minutes=reminder["lead_minutes"])
        if (
            fire <= now
            and (now - fire) <= timedelta(seconds=59)
            and reminder.get("last_fired") != fire.isoformat()
        ):
            return fire
    return None


def render(reminder):
    """Fill {time}, {lead} and {name} placeholders in a reminder's text."""
    ctx = {
        "time": f"{reminder['hour']:02d}:{reminder['minute']:02d}",
        "lead": reminder["lead_minutes"],
        "name": reminder["name"],
    }
    try:
        title = reminder["title"].format(**ctx)
    except (KeyError, IndexError, ValueError):
        title = reminder["title"]
    try:
        body = reminder["body"].format(**ctx)
    except (KeyError, IndexError, ValueError):
        body = reminder["body"]
    return title, body


class Storage:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.data = {"guilds": {}}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.data = {"guilds": {}}

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def get_guild(self, guild_id):
        gid = str(guild_id)
        if gid not in self.data["guilds"]:
            self.data["guilds"][gid] = {"channel_id": None, "reminders": []}
            self.save()
        return self.data["guilds"][gid]

    def set_channel(self, guild_id, channel_id):
        g = self.get_guild(guild_id)
        first_time = g["channel_id"] is None
        g["channel_id"] = channel_id
        if first_time and not g["reminders"]:
            for r in DEFAULT_REMINDERS:
                g["reminders"].append(self._new_reminder(r))
        self.save()

    def _new_reminder(self, fields):
        r = dict(fields)
        r["id"] = uuid.uuid4().hex[:8]
        r["last_fired"] = None
        return r

    def add_reminder(self, guild_id, fields):
        g = self.get_guild(guild_id)
        r = self._new_reminder(fields)
        g["reminders"].append(r)
        self.save()
        return r

    def remove_reminder(self, guild_id, reminder_id):
        g = self.get_guild(guild_id)
        before = len(g["reminders"])
        g["reminders"] = [r for r in g["reminders"] if r["id"] != reminder_id]
        self.save()
        return len(g["reminders"]) != before

    def mark_fired(self, guild_id, reminder_id, fire_iso):
        g = self.get_guild(guild_id)
        for r in g["reminders"]:
            if r["id"] == reminder_id:
                r["last_fired"] = fire_iso
        self.save()
