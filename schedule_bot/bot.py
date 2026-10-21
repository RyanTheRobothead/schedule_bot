"""The Discord client: invocation handling, message refreshes, nagging and closing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import tasks

from .config import Config
from .dates import MAX_DAYS, DateRangeError, parse_date_range
from .db import Database
from .invocation import (
    DEFAULT_TITLE,
    Invocation,
    InvocationError,
    extract_mentions,
    parse_invocation,
    parse_nag,
)
from .models import Poll
from .nagging import first_nag_at, next_nag_at
from .render import closing_text, mentions, poll_embed, range_label
from .views import (
    CloseButton,
    DaySelect,
    DefaultSelect,
    FineTuneButton,
    SameAsLastButton,
    StatusButton,
    poll_view,
)

log = logging.getLogger(__name__)

REFRESH_DELAY = 1.5  # seconds; coalesces bursts of clicks into one message edit
PING_LIMIT = 50  # most people pinged in one message, to stay under the 2000-char content cap
ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)

HELP_TEXT = f"""\
**How to use me**
Mention me — `@schedule_bot [dates] [@people/@roles] ["title"] [nag=…]` — or use `/schedule`.

**Dates** (default: next Monday–Sunday)
`next week` · `this week` · `weekend` · `next 10 days` · `2 weeks` · `fri-sun` · `oct 30 - nov 3` · `11/1-11/7` · `2026-11-01..2026-11-07` · `friday`
Up to {MAX_DAYS} days.

**Who** — mention users and/or roles to invite just them; otherwise everyone who can see this channel is invited.

**Reminders** — by default I'll ping anyone who hasn't answered once a day, in the evening. \
`nag=12h`, `nag=2d`, `nag=9am` (daily at 9am), `nag=2d@7pm`, or `nag=off`.

**Answering** — click your usual availability on the poll (or 🔁 to copy your last answer), then use the day menus that pop up for the exceptions.
Example: `@schedule_bot fri-sun "One-shot" @Players nag=12h`"""

SendPoll = Callable[..., Awaitable[discord.Message]]


class ScheduleBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.members = True  # to expand roles and list who can see a channel
        intents.message_content = True  # so mentions via the bot's role still carry text
        super().__init__(intents=intents, allowed_mentions=ALLOWED_MENTIONS)
        self.config = config
        self.db = Database(config.db_path)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(schedule_command)
        self._pending_refresh: set[int] = set()
        self._background: set[asyncio.Task] = set()

    async def setup_hook(self) -> None:
        self.add_dynamic_items(StatusButton, SameAsLastButton, FineTuneButton, CloseButton, DefaultSelect, DaySelect)
        await self.tree.sync()
        # dateparser loads its locale data on first use; do it now rather than inside
        # a /schedule interaction, which has to respond within 3 seconds.
        with contextlib.suppress(DateRangeError):  # e.g. "in the past" in early January
            parse_date_range("jan 1 - jan 2", self.config.today())
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
            users = [m for m in message.mentions if m.id != self.user.id]
            roles = [r for r in message.role_mentions if not self._is_bot_role(r)]
            poll = await self._prepare_poll(message.channel, message.author, inv, users, roles)
        except (DateRangeError, InvocationError) as e:
            await message.reply(f"⚠️ {e}\nSay `@{self.user.name} help` for examples.", mention_author=False)
            return
        try:
            sent = await self._post_poll(poll, message.channel.send)
        except discord.HTTPException as e:
            await message.reply(f"⚠️ I couldn't post the poll: {e.text or e}", mention_author=False)
            return
        log.info("Created poll %s in #%s via mention (message %s)", poll.id, message.channel, sent.id)

    async def schedule_from_interaction(
        self, interaction: discord.Interaction, dates: str, title: str, invite: str, nag: str
    ) -> None:
        guild, channel = interaction.guild, interaction.channel
        try:
            inv = Invocation(title=title.strip() or DEFAULT_TITLE, date_text=dates)
            if nag.strip():
                inv.nag_seconds, inv.nag_time = parse_nag(nag)
            user_ids, role_ids = extract_mentions(invite)
            users = [m for uid in user_ids if (m := guild.get_member(uid)) is not None]
            roles = [r for rid in role_ids if (r := guild.get_role(rid)) is not None and not self._is_bot_role(r)]
            if invite.strip() and not (users or roles) and "@everyone" not in invite and "@here" not in invite:
                raise InvocationError("I couldn't find anyone in `invite` — pick people or roles with @.")
            poll = await self._prepare_poll(channel, interaction.user, inv, users, roles)
        except (DateRangeError, InvocationError) as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        async def send(**kwargs) -> discord.Message:
            await interaction.response.send_message(**kwargs, allowed_mentions=ALLOWED_MENTIONS)
            return await interaction.original_response()

        try:
            sent = await self._post_poll(poll, send)
        except discord.HTTPException as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"⚠️ I couldn't post the poll: {e.text or e}", ephemeral=True)
            return
        log.info("Created poll %s in #%s via /schedule (message %s)", poll.id, channel, sent.id)

    async def _prepare_poll(self, channel, author: discord.abc.User, inv: Invocation, users, roles) -> Poll:
        """Validate an invocation and store the poll (without a message yet)."""
        start, end = parse_date_range(inv.date_text, self.config.today(), self.config.date_order)
        participants = await self._resolve_participants(channel, users, roles)
        if not participants:
            raise InvocationError("I couldn't find anyone to invite.")

        interval = self.config.default_nag_seconds if inv.nag_seconds is None else inv.nag_seconds
        nag_time = inv.nag_time or self.config.default_nag_time
        now = time.time()
        return self.db.create_poll(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            creator_id=author.id,
            title=inv.title,
            start=start,
            end=end,
            nag_interval=interval,
            nag_time=nag_time,
            next_nag_at=first_nag_at(now, interval, nag_time, self.config.tz),
            created_at=now,
            participants=participants,
        )

    async def _post_poll(self, poll: Poll, send: SendPoll) -> discord.Message:
        """Post a stored poll with ``send``; the poll is deleted again if that fails."""
        participants = self.db.participants(poll.id)
        try:
            sent = await send(
                content=f"📣 {mentions(participants, PING_LIMIT)} — when can you make **{poll.title}**?",
                embed=poll_embed(poll, self.db.responses(poll.id)),
                view=poll_view(poll),
            )
        except discord.HTTPException:
            self.db.delete_poll(poll.id)
            log.exception("Failed to post poll %s", poll.id)
            raise
        self.db.set_message_id(poll.id, sent.id)
        return sent

    async def _resolve_participants(self, channel, users, roles) -> set[int]:
        if users or roles:
            ids = {m.id for m in users if not m.bot}
            for role in roles:
                ids.update(m.id for m in role.members if not m.bot)
            return ids

        if isinstance(channel, discord.Thread):
            thread_members = await channel.fetch_members()
            members = [channel.guild.get_member(tm.id) for tm in thread_members]
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
        self.db.set_next_nag(
            poll.id, next_nag_at(poll.next_nag_at, now, poll.nag_interval, poll.nag_time, self.config.tz)
        )
        waiting = [uid for uid, r in self.db.responses(poll.id).items() if not r.has_answered]
        if not waiting:
            return
        message = await self._poll_message(poll)
        if message is None:
            return
        await message.channel.send(
            f"⏰ {mentions(waiting, PING_LIMIT)} — still need your availability for **{poll.title}** "
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


@app_commands.command(name="schedule", description="Start a poll to find a day that works for everyone")
@app_commands.describe(
    dates="e.g. next week, fri-sun, oct 30 - nov 3, next 10 days (default: next Mon–Sun)",
    title='What it\'s for, e.g. "One-shot" (default: Game night)',
    invite="@people and/or @roles to invite (default: everyone who can see this channel)",
    nag="Reminder interval: daily, 12h, 2d, 9am, 2d@7pm, off (default: daily, evenings)",
)
@app_commands.guild_only()
async def schedule_command(
    interaction: discord.Interaction, dates: str = "", title: str = "", invite: str = "", nag: str = ""
) -> None:
    bot: ScheduleBot = interaction.client  # type: ignore[assignment]
    await bot.schedule_from_interaction(interaction, dates, title, invite, nag)
