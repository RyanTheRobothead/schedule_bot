"""The Discord client: invocation handling, message refreshes, nagging and closing."""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import tasks

from .config import Config
from .dates import MAX_DAYS, DateRangeError, parse_date_range
from .db import Database
from .invocation import InvocationError, parse_invocation
from .models import Poll
from .render import closing_text, mentions, poll_embed, range_label
from .views import CloseButton, DaySelect, DefaultSelect, FineTuneButton, StatusButton, poll_view

log = logging.getLogger(__name__)

REFRESH_DELAY = 1.5  # seconds; coalesces bursts of clicks into one message edit

HELP_TEXT = f"""\
**How to use me**
Mention me to start a scheduling poll: `@schedule_bot [dates] [@people/@roles] ["title"] [nag=…]`

**Dates** (default: next Monday–Sunday)
`next week` · `this week` · `weekend` · `next 10 days` · `2 weeks` · `fri-sun` · `oct 30 - nov 3` · `11/1-11/7` · `2026-11-01..2026-11-07` · `friday`
Up to {MAX_DAYS} days.

**Who** — mention users and/or roles to invite just them; otherwise everyone who can see this channel is invited.

**Reminders** — by default I'll ping anyone who hasn't answered once a day. `nag=12h`, `nag=2d`, or `nag=off`.

**Answering** — click your usual availability on the poll, then use the day menus that pop up for the exceptions.
Example: `@schedule_bot fri-sun "One-shot" @Players nag=12h`"""


class ScheduleBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.members = True  # to expand roles and list who can see a channel
        intents.message_content = True  # so mentions via the bot's role still carry text
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.config = config
        self.db = Database(config.db_path)
        self._pending_refresh: set[int] = set()
        self._background: set[asyncio.Task] = set()

    async def setup_hook(self) -> None:
        self.add_dynamic_items(StatusButton, FineTuneButton, CloseButton, DefaultSelect, DaySelect)
        self.housekeeping.change_interval(minutes=self.config.housekeeping_minutes)
        self.housekeeping.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")

    # --- invocation --------------------------------------------------------

    def _is_bot_role(self, role: discord.Role) -> bool:
        return role.tags is not None and role.tags.bot_id == self.user.id

    def _is_addressed(self, message: discord.Message) -> bool:
        return self.user in message.mentions or any(self._is_bot_role(r) for r in message.role_mentions)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not self._is_addressed(message):
            return
        try:
            inv = parse_invocation(message.content)
            if inv.help:
                await message.reply(HELP_TEXT, mention_author=False)
                return
            start, end = parse_date_range(inv.date_text, self.config.today(), self.config.date_order)
        except (DateRangeError, InvocationError) as e:
            await message.reply(f"⚠️ {e}\nSay `@{self.user.name} help` for examples.", mention_author=False)
            return

        participants = await self._resolve_participants(message)
        if not participants:
            await message.reply("⚠️ I couldn't find anyone to invite.", mention_author=False)
            return

        nag = self.config.default_nag_seconds if inv.nag_seconds is None else inv.nag_seconds
        poll = self.db.create_poll(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            creator_id=message.author.id,
            title=inv.title,
            start=start,
            end=end,
            nag_interval=nag,
            participants=participants,
        )
        try:
            sent = await message.channel.send(
                content=f"📣 {mentions(participants)} — when can you make **{poll.title}**?",
                embed=poll_embed(poll, self.db.responses(poll.id)),
                view=poll_view(poll),
            )
        except discord.HTTPException:
            self.db.delete_poll(poll.id)
            raise
        self.db.set_message_id(poll.id, sent.id)
        log.info("Created poll %s in #%s for %s", poll.id, message.channel, range_label(start, end))

    async def _resolve_participants(self, message: discord.Message) -> set[int]:
        users = [m for m in message.mentions if m.id != self.user.id]
        roles = [r for r in message.role_mentions if not self._is_bot_role(r)]
        if users or roles:
            ids = {m.id for m in users if not m.bot}
            for role in roles:
                ids.update(m.id for m in role.members if not m.bot)
            return ids

        channel = message.channel
        if isinstance(channel, discord.Thread):
            thread_members = await channel.fetch_members()
            members = [message.guild.get_member(tm.id) for tm in thread_members]
        else:
            members = getattr(channel, "members", [])
        return {m.id for m in members if m is not None and not m.bot}

    # --- interactions helpers ---------------------------------------------

    async def open_poll_for(self, interaction: discord.Interaction, poll_id: int) -> Poll | None:
        """Look up a poll for a component interaction, replying with an error if it's unusable."""
        poll = self.db.get_poll(poll_id)
        if poll is None or poll.closed:
            await interaction.response.send_message("This poll is closed.", ephemeral=True)
            return None
        return poll

    # --- updating the poll message -------------------------------------------

    def request_refresh(self, poll_id: int) -> None:
        if poll_id in self._pending_refresh:
            return
        self._pending_refresh.add(poll_id)
        task = asyncio.create_task(self._refresh_later(poll_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _refresh_later(self, poll_id: int) -> None:
        await asyncio.sleep(REFRESH_DELAY)
        self._pending_refresh.discard(poll_id)
        poll = self.db.get_poll(poll_id)
        if poll is None:
            return
        try:
            await self._edit_poll_message(poll)
        except discord.HTTPException:
            log.exception("Failed to refresh poll %s", poll_id)

    async def _poll_message(self, poll: Poll) -> discord.PartialMessage | None:
        channel = self.get_channel(poll.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(poll.channel_id)
            except (discord.NotFound, discord.Forbidden):
                return None
        return channel.get_partial_message(poll.message_id)  # type: ignore[union-attr]

    async def _edit_poll_message(self, poll: Poll) -> None:
        message = await self._poll_message(poll)
        if message is None:
            return
        view = None if poll.closed else poll_view(poll)
        try:
            await message.edit(embed=poll_embed(poll, self.db.responses(poll.id)), view=view)
        except discord.NotFound:
            log.info("Poll %s message was deleted; closing it", poll.id)
            self.db.close_poll(poll.id)

    # --- closing & nagging ---------------------------------------------------

    async def close_poll(self, poll: Poll) -> None:
        self.db.close_poll(poll.id)
        poll = self.db.get_poll(poll.id)
        await self._edit_poll_message(poll)
        message = await self._poll_message(poll)
        if message is not None:
            try:
                await message.reply(closing_text(poll, self.db.responses(poll.id)), mention_author=False)
            except discord.HTTPException:
                log.exception("Failed to announce closing of poll %s", poll.id)

    async def nag(self, poll: Poll) -> None:
        now = time.time()
        self.db.set_next_nag(poll.id, now + poll.nag_interval)
        waiting = [uid for uid, r in self.db.responses(poll.id).items() if not r.has_answered]
        if not waiting:
            return
        message = await self._poll_message(poll)
        if message is None:
            return
        await message.channel.send(
            f"⏰ {mentions(waiting)} — still need your availability for **{poll.title}** "
            f"({range_label(poll.start, poll.end)}): {poll.jump_url}"
        )

    @tasks.loop(minutes=5)
    async def housekeeping(self) -> None:
        today = self.config.today()
        now = time.time()
        for poll in self.db.open_polls():
            try:
                if poll.end < today:
                    await self.close_poll(poll)
                elif poll.nag_interval and poll.next_nag_at is not None and poll.next_nag_at <= now:
                    await self.nag(poll)
            except Exception:
                log.exception("Housekeeping failed for poll %s", poll.id)

    @housekeeping.before_loop
    async def _before_housekeeping(self) -> None:
        await self.wait_until_ready()
