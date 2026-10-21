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
from .health import start_health_server
from .invocation import (
    DEFAULT_TITLE,
    Invocation,
    InvocationError,
    extract_mentions,
    parse_invocation,
    parse_lead,
    parse_nag,
)
from .models import Poll, Recurrence
from .nagging import first_nag_at, next_nag_at
from .recurrence import (
    DEFAULT_LEAD_DAYS,
    DEFAULT_POST_TIME,
    RecurrenceError,
    catch_up,
    next_period,
    period_label,
    post_at,
    repeat_label,
    upcoming_post,
    validate,
)
from .render import closing_text, mentions, poll_embed, range_label, recurrence_line, time_label, when_label
from .views import (
    CloseButton,
    DaySelect,
    DefaultSelect,
    FineTuneButton,
    SameAsLastButton,
    SkipNextButton,
    SkipPollButton,
    StatusButton,
    StopRepeatingButton,
    UndoSkipButton,
    poll_view,
    recurrence_view,
    recurring_list_view,
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

**Repeating** — add `every week`, `every 2 weeks`, `every other week`, `every friday` or `every weekend` and I'll post a fresh poll \
for each period automatically, {DEFAULT_LEAD_DAYS} days ahead at {time_label(DEFAULT_POST_TIME)} unless you say `lead=2d`, `lead=1w`, or `lead=3d@9am`. \
`@schedule_bot recurring` (or `/recurring`) lists them, with buttons to ⏭️ skip the next one or 🛑 stop it. \
A repeating poll that's already up has its own ⏭️ **Skip this time** button.

**Answering** — click your usual availability on the poll (or 🔁 to copy your last answer), then use the day menus that pop up for the exceptions.
Example: `@schedule_bot fri-sun "One-shot" @Players nag=12h`
Example: `@schedule_bot every week "Game night" @Players lead=3d`"""
_ERRORS = (DateRangeError, InvocationError, RecurrenceError)
LIST_HEADER = "🔁 **Repeating polls**"
NO_RECURRING = "No repeating polls here. Start one with e.g. `@schedule_bot every week`."

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
        self.tree.add_command(recurring_command)
        self._pending_refresh: set[int] = set()
        self._background: set[asyncio.Task] = set()
        self._health_runner = None
        self._recurrence_locks: dict[int, asyncio.Lock] = {}

    async def setup_hook(self) -> None:
        self.add_dynamic_items(
            StatusButton,
            SameAsLastButton,
            FineTuneButton,
            CloseButton,
            DefaultSelect,
            DaySelect,
            StopRepeatingButton,
            SkipPollButton,
            SkipNextButton,
            UndoSkipButton,
        )
        await self.tree.sync()
        # dateparser loads its locale data on first use; do it now rather than inside
        # a /schedule interaction, which has to respond within 3 seconds.
        with contextlib.suppress(DateRangeError):  # e.g. "in the past" in early January
            parse_date_range("jan 1 - jan 2", self.config.today())
        self.housekeeping.change_interval(minutes=self.config.housekeeping_minutes)
        self.housekeeping.start()
        if self.config.health_port:
            self._health_runner = await start_health_server(self, self.config.health_host, self.config.health_port)
            log.info("Health check on http://%s:%s/health", self.config.health_host, self.config.health_port)

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await super().close()

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
            if inv.list_recurring:
                await message.reply(**self._recurring_list(message.guild.id), mention_author=False)
                return
            users = [m for m in message.mentions if m.id != self.user.id]
            roles = [r for r in message.role_mentions if not self._is_bot_role(r)]
            if inv.repeat_weeks is not None:
                rec = await self._create_recurrence(message.channel, message.author, inv, users, roles)
            else:
                poll = await self._prepare_poll(message.channel, message.author, inv, users, roles)
        except _ERRORS as e:
            await message.reply(f"⚠️ {e}\nSay `@{self.user.name} help` for examples.", mention_author=False)
            return
        if inv.repeat_weeks is not None:
            await message.reply(**self._recurrence_created(rec), mention_author=False)
            await self._run_recurrence(rec)
            return
        try:
            sent = await self._post_poll(poll, message.channel.send)
        except discord.HTTPException as e:
            await message.reply(f"⚠️ I couldn't post the poll: {e.text or e}", mention_author=False)
            return
        log.info("Created poll %s in #%s via mention (message %s)", poll.id, message.channel, sent.id)

    async def schedule_from_interaction(
        self,
        interaction: discord.Interaction,
        dates: str,
        title: str,
        invite: str,
        nag: str,
        repeat_weeks: int | None = None,
        lead: str = "",
    ) -> None:
        guild, channel = interaction.guild, interaction.channel
        try:
            inv = Invocation(title=title.strip() or DEFAULT_TITLE, date_text=dates, repeat_weeks=repeat_weeks)
            if nag.strip():
                inv.nag_seconds, inv.nag_time = parse_nag(nag)
            if lead.strip():
                if repeat_weeks is None:
                    raise InvocationError("`lead` only applies to repeating polls; pick a `repeat` too.")
                inv.lead_days, inv.lead_time = parse_lead(lead)
            user_ids, role_ids = extract_mentions(invite)
            users = [m for uid in user_ids if (m := guild.get_member(uid)) is not None]
            roles = [r for rid in role_ids if (r := guild.get_role(rid)) is not None and not self._is_bot_role(r)]
            if invite.strip() and not (users or roles) and "@everyone" not in invite and "@here" not in invite:
                raise InvocationError("I couldn't find anyone in `invite` — pick people or roles with @.")
            if repeat_weeks is not None:
                rec = await self._create_recurrence(channel, interaction.user, inv, users, roles)
            else:
                poll = await self._prepare_poll(channel, interaction.user, inv, users, roles)
        except _ERRORS as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        if repeat_weeks is not None:
            await interaction.response.send_message(**self._recurrence_created(rec))
            await self._run_recurrence(rec)
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

        interval, nag_time = self._nag_settings(inv)
        return self._store_poll(channel, author.id, inv.title, start, end, interval, nag_time, participants)

    def _nag_settings(self, inv: Invocation):
        interval = self.config.default_nag_seconds if inv.nag_seconds is None else inv.nag_seconds
        return interval, inv.nag_time or self.config.default_nag_time

    def _store_poll(
        self, channel, creator_id, title, start, end, interval, nag_time, participants, recurrence_id=None
    ) -> Poll:
        now = time.time()
        return self.db.create_poll(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            creator_id=creator_id,
            title=title,
            start=start,
            end=end,
            nag_interval=interval,
            nag_time=nag_time,
            next_nag_at=first_nag_at(now, interval, nag_time, self.config.tz),
            created_at=now,
            participants=participants,
            recurrence_id=recurrence_id,
        )

    def _render_poll(self, poll: Poll):
        """The embed and view for a poll's message, including recurrence info while it's repeating."""
        rec = self.db.get_recurrence(poll.recurrence_id) if poll.recurrence_id else None
        repeating = rec is not None and rec.active and not poll.closed
        note = None
        if repeating:
            nxt = upcoming_post(
                rec.period_start, rec.lead_days, rec.post_time, rec.interval_weeks, time.time(), self.config.tz
            )
            note = f"Repeats {repeat_label(rec.interval_weeks)}, next poll {when_label(nxt, self.config.tz)}"
        embed = poll_embed(poll, self.db.responses(poll.id), repeat_note=note)
        view = None if poll.closed else poll_view(poll, rec.id if repeating else None)
        return embed, view

    async def _post_poll(self, poll: Poll, send: SendPoll) -> discord.Message:
        """Post a stored poll with ``send``; the poll is deleted again if that fails."""
        participants = self.db.participants(poll.id)
        try:
            embed, view = self._render_poll(poll)
            sent = await send(
                content=f"📣 {mentions(participants, PING_LIMIT)} — when can you make **{poll.title}**?",
                embed=embed,
                view=view,
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

    # --- recurring polls -----------------------------------------------------

    async def _create_recurrence(self, channel, author, inv: Invocation, users, roles) -> Recurrence:
        # Unclamped, so "every week this week" repeats Mon–Sun rather than from today.
        start, end = parse_date_range(inv.date_text, self.config.today(), self.config.date_order, clamp=False)
        period_days = (end - start).days + 1
        lead_days = DEFAULT_LEAD_DAYS if inv.lead_days is None else inv.lead_days
        validate(period_days, inv.repeat_weeks, lead_days)
        if not await self._resolve_participants(channel, users, roles):
            raise InvocationError("I couldn't find anyone to invite.")
        interval, nag_time = self._nag_settings(inv)
        return self.db.create_recurrence(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            creator_id=author.id,
            title=inv.title,
            period_start=catch_up(start, period_days, inv.repeat_weeks, self.config.today()),
            period_days=period_days,
            interval_weeks=inv.repeat_weeks,
            lead_days=lead_days,
            post_time=inv.lead_time or DEFAULT_POST_TIME,
            nag_interval=interval,
            nag_time=nag_time,
            user_ids={u.id for u in users},
            role_ids={r.id for r in roles},
        )

    def _recurrence_created(self, rec: Recurrence) -> dict:
        first = post_at(rec.period_start, rec.lead_days, rec.post_time, self.config.tz)
        ahead = "on the first day" if rec.lead_days == 0 else f"{rec.lead_days} day{'s' * (rec.lead_days != 1)} ahead"
        when = "is going out now" if first <= time.time() else f"goes out {when_label(first, self.config.tz)}"
        content = (
            f"🔁 Got it: I'll post a **{rec.title}** poll for {period_label(rec.period_start, rec.period_days)} "
            f"{repeat_label(rec.interval_weeks)}, {ahead} at {time_label(rec.post_time)}. "
            f"The first one ({range_label(rec.period_start, rec.period_end)}) {when}."
        )
        return dict(content=content, view=recurrence_view([rec.id]), allowed_mentions=discord.AllowedMentions.none())

    def _recurring_list(self, guild_id: int) -> dict:
        recs = self.db.active_recurrences(guild_id)
        if not recs:
            return dict(content=NO_RECURRING)  # no view key: send_message(view=None) raises
        # period_start is always the next period still to be posted; if its time has
        # passed, housekeeping will post it within a few minutes.
        now = time.time()
        lines = [
            recurrence_line(r, max(now, post_at(r.period_start, r.lead_days, r.post_time, self.config.tz)), self.config.tz)
            for r in recs
        ]
        return dict(
            content=LIST_HEADER + "\n" + "\n".join(lines)[:1900],
            view=recurring_list_view(recs),  # buttons for the first 5; Discord allows 5 rows
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _run_recurrence(self, rec: Recurrence) -> None:
        """Post the recurrence's current period if it's due, then move on to the next period.

        Idempotent: a period that already has a poll isn't posted again, so a crash or
        error between posting and advancing can neither double-post nor skip a week.
        The lock covers creation and housekeeping running it at the same moment.
        """
        async with self._recurrence_locks.setdefault(rec.id, asyncio.Lock()):
            rec = self.db.get_recurrence(rec.id)
            if rec is not None and rec.active:
                await self._run_recurrence_unlocked(rec)

    async def _run_recurrence_unlocked(self, rec: Recurrence) -> None:
        today = self.config.today()
        start = catch_up(rec.period_start, rec.period_days, rec.interval_weeks, today)
        if start != rec.period_start:
            self.db.set_recurrence_period(rec.id, start)
        if time.time() < post_at(start, rec.lead_days, rec.post_time, self.config.tz):
            return
        end = start + (rec.period_end - rec.period_start)

        if not self.db.recurrence_has_poll(rec.id, end):
            channel = self.get_channel(rec.channel_id)
            try:
                channel = channel or await self.fetch_channel(rec.channel_id)
            except (discord.NotFound, discord.Forbidden):
                log.warning("Recurrence %s: channel %s is gone; stopping it", rec.id, rec.channel_id)
                self.db.deactivate_recurrence(rec.id)
                return
            guild = channel.guild
            users = [m for uid in rec.user_ids if (m := guild.get_member(uid)) is not None]
            roles = [r for rid in rec.role_ids if (r := guild.get_role(rid)) is not None]
            participants = set()
            if users or roles or not (rec.user_ids or rec.role_ids):
                participants = await self._resolve_participants(channel, users, roles)
            if not participants:
                log.warning("Recurrence %s: nobody left to invite; stopping it", rec.id)
                self.db.deactivate_recurrence(rec.id)
                with contextlib.suppress(discord.HTTPException):
                    await channel.send(
                        f"🛑 Stopped repeating **{rec.title}**: none of the people or roles it invited are here anymore.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                return
            interval = rec.nag_interval
            poll = self._store_poll(
                channel, rec.creator_id, rec.title, max(start, today), end, interval, rec.nag_time, participants, rec.id
            )
            try:
                sent = await self._post_poll(poll, channel.send)
            except (discord.NotFound, discord.Forbidden):
                log.warning("Recurrence %s: can't post in channel %s; stopping it", rec.id, rec.channel_id)
                self.db.deactivate_recurrence(rec.id)
                return
            except discord.HTTPException:
                return  # _post_poll removed the poll; try again next time round
            log.info("Recurrence %s posted poll %s (message %s)", rec.id, poll.id, sent.id)

        self.db.set_recurrence_period(rec.id, next_period(start, rec.interval_weeks))

    async def _check_manager(self, interaction: discord.Interaction, creator_id: int, what: str) -> bool:
        """Creator or moderator only; tells anyone else so."""
        if interaction.user.id == creator_id or interaction.permissions.manage_messages:
            return True
        await interaction.response.send_message(f"Only <@{creator_id}> (or a moderator) can {what}.", ephemeral=True)
        return False

    async def _active_recurrence_for(self, interaction: discord.Interaction, recurrence_id: int):
        rec = self.db.get_recurrence(recurrence_id)
        if rec is None or not rec.active:
            await interaction.response.send_message("That poll has stopped repeating.", ephemeral=True)
            return None
        return rec

    async def _announce_from_button(self, interaction: discord.Interaction, rec: Recurrence, notice: dict) -> None:
        """Post ``notice`` publicly, updating a /recurring list or confirmation the button was on."""
        message = interaction.message
        if message is None or self.db.poll_by_message(message.id) is not None:
            await interaction.response.send_message(**notice)  # polls refresh themselves
            return
        if message.content.startswith((LIST_HEADER, NO_RECURRING)):
            await interaction.response.edit_message(**{"view": None, **self._recurring_list(rec.guild_id)})
        else:  # a "Got it" confirmation or an earlier skip notice
            await interaction.response.edit_message(view=None)
        await interaction.followup.send(**notice)

    async def stop_recurrence(self, interaction: discord.Interaction, recurrence_id: int) -> None:
        rec = await self._active_recurrence_for(interaction, recurrence_id)
        if rec is None or not await self._check_manager(interaction, rec.creator_id, "stop this"):
            return
        self.db.deactivate_recurrence(rec.id)
        rec.active = False
        notice = dict(
            content=f"🛑 {interaction.user.mention} stopped repeating **{rec.title}**. Polls already posted stay open.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._announce_from_button(interaction, rec, notice)
        for poll in self.db.open_polls_for_recurrence(rec.id):
            self.request_refresh(poll.id)

    def _next_post_text(self, rec: Recurrence) -> str:
        when = when_label(max(time.time(), post_at(rec.period_start, rec.lead_days, rec.post_time, self.config.tz)), self.config.tz)
        return f"The next poll goes out {when}, for {range_label(rec.period_start, rec.period_end)}."

    async def skip_poll(self, interaction: discord.Interaction, poll_id: int) -> None:
        """Close an already-posted repeating poll as skipped."""
        poll = await self.open_poll_for(interaction, poll_id)
        if poll is None or not await self._check_manager(interaction, poll.creator_id, "skip this one"):
            return
        self.db.close_poll(poll.id, skipped=True)
        rec = self.db.get_recurrence(poll.recurrence_id) if poll.recurrence_id else None
        after = f" {self._next_post_text(rec)}" if rec is not None and rec.active else ""
        await interaction.response.send_message(
            f"⏭️ {interaction.user.mention} skipped **{poll.title}** this time "
            f"({range_label(poll.start, poll.end)}); no more reminders for it.{after}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._edit_poll_message(self.db.get_poll(poll.id))

    async def skip_next(self, interaction: discord.Interaction, recurrence_id: int) -> None:
        """Skip a recurrence's next period before its poll is posted."""
        rec = await self._active_recurrence_for(interaction, recurrence_id)
        if rec is None or not await self._check_manager(interaction, rec.creator_id, "skip this"):
            return
        async with self._recurrence_locks.setdefault(rec.id, asyncio.Lock()):
            rec = self.db.get_recurrence(rec.id)
            start = catch_up(rec.period_start, rec.period_days, rec.interval_weeks, self.config.today())
            if self.db.recurrence_has_poll(rec.id, start + (rec.period_end - rec.period_start)):
                start = next_period(start, rec.interval_weeks)  # already posted: use that poll's skip button
            rec.period_start = next_period(start, rec.interval_weeks)
            self.db.set_recurrence_period(rec.id, rec.period_start)
        skipped_end = start + (rec.period_end - rec.period_start)
        notice = dict(
            content=f"⏭️ {interaction.user.mention} skipped **{rec.title}** for {range_label(start, skipped_end)}. "
            + self._next_post_text(rec),
            view=discord.ui.View(timeout=None).add_item(UndoSkipButton(rec.id, start)),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._announce_from_button(interaction, rec, notice)

    async def undo_skip(self, interaction: discord.Interaction, recurrence_id: int, skipped_start) -> None:
        rec = await self._active_recurrence_for(interaction, recurrence_id)
        if rec is None or not await self._check_manager(interaction, rec.creator_id, "undo this"):
            return
        async with self._recurrence_locks.setdefault(rec.id, asyncio.Lock()):
            rec = self.db.get_recurrence(rec.id)
            skipped_end = skipped_start + (rec.period_end - rec.period_start)
            if (
                rec.period_start != next_period(skipped_start, rec.interval_weeks)
                or skipped_end < self.config.today()
                or self.db.recurrence_has_poll(rec.id, skipped_end)
            ):
                await interaction.response.send_message("It's too late to undo that skip.", ephemeral=True)
                return
            self.db.set_recurrence_period(rec.id, skipped_start)
        await interaction.response.edit_message(view=None)
        await interaction.followup.send(
            f"↩️ {interaction.user.mention} un-skipped **{rec.title}** for "
            f"{range_label(skipped_start, skipped_end)}. " + self._next_post_text(self.db.get_recurrence(rec.id)),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._run_recurrence(rec)  # posts right away if it's already due

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
        embed, view = self._render_poll(poll)
        try:
            await message.edit(embed=embed, view=view)
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
        for rec in self.db.active_recurrences():
            try:
                await self._run_recurrence(rec)
            except Exception:
                log.exception("Housekeeping failed for recurrence %s", rec.id)
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
    repeat="Post a fresh poll for these days automatically, on repeat",
    lead=f"For repeating polls, how far ahead to post: 2d, 1w, 3d@9am (default: {DEFAULT_LEAD_DAYS}d at 6pm)",
)
@app_commands.choices(
    repeat=[
        app_commands.Choice(name="Every week", value=1),
        app_commands.Choice(name="Every 2 weeks", value=2),
        app_commands.Choice(name="Every 3 weeks", value=3),
        app_commands.Choice(name="Every 4 weeks", value=4),
    ]
)
@app_commands.guild_only()
async def schedule_command(
    interaction: discord.Interaction,
    dates: str = "",
    title: str = "",
    invite: str = "",
    nag: str = "",
    repeat: app_commands.Choice[int] | None = None,
    lead: str = "",
) -> None:
    bot: ScheduleBot = interaction.client  # type: ignore[assignment]
    await bot.schedule_from_interaction(
        interaction, dates, title, invite, nag, repeat.value if repeat else None, lead
    )


@app_commands.command(name="recurring", description="List this server's repeating polls (with buttons to stop them)")
@app_commands.guild_only()
async def recurring_command(interaction: discord.Interaction) -> None:
    bot: ScheduleBot = interaction.client  # type: ignore[assignment]
    await interaction.response.send_message(**bot._recurring_list(interaction.guild.id), ephemeral=True)
