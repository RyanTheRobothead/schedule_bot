"""Buttons and select menus.

Everything is a ``DynamicItem`` whose custom_id encodes the poll id, so components keep
working after the bot restarts (including on users' already-open personal panels).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import discord

from .models import STATUS_ORDER, Poll, Recurrence, Status, UserResponse, carry_over
from .render import day_label, panel_text, range_label

if TYPE_CHECKING:
    from .bot import ScheduleBot

_BUTTON_STYLE = {
    Status.PREFERRED: discord.ButtonStyle.success,
    Status.AVAILABLE: discord.ButtonStyle.primary,
    Status.TENTATIVE: discord.ButtonStyle.secondary,
    Status.UNAVAILABLE: discord.ButtonStyle.danger,
}

_DAY_SELECT_PLACEHOLDER = {
    Status.PREFERRED: "⭐ Preferred on…",
    Status.AVAILABLE: "✅ Available on…",
    Status.TENTATIVE: "❔ Tentative on…",
    Status.UNAVAILABLE: "❌ Unavailable on…",
}


def poll_view(poll: Poll, repeating_id: int | None = None) -> discord.ui.View:
    """Components attached to the public poll message.

    ``repeating_id`` adds a stop button for the still-active recurrence that posted this poll.
    """
    view = discord.ui.View(timeout=None)
    for status in STATUS_ORDER:
        view.add_item(StatusButton(poll.id, status))
    view.add_item(SameAsLastButton(poll.id))
    view.add_item(FineTuneButton(poll.id))
    view.add_item(CloseButton(poll.id))
    if poll.recurrence_id is not None:
        view.add_item(SkipPollButton(poll.id))
    if repeating_id is not None:
        view.add_item(StopRepeatingButton(repeating_id, row=1))
    return view


def recurrence_view(recurrence_ids: list[int]) -> discord.ui.View:
    """The stop button under a "Got it" confirmation."""
    view = discord.ui.View(timeout=None)
    for rid in recurrence_ids[:25]:
        view.add_item(StopRepeatingButton(rid))
    return view


def recurring_list_view(recs: list[Recurrence]) -> discord.ui.View:
    """A row per recurrence in the /recurring list: skip its next period, or stop it."""
    view = discord.ui.View(timeout=None)
    for row, rec in enumerate(recs[:5]):
        dates = range_label(rec.period_start, rec.period_end)
        view.add_item(SkipNextButton(rec.id, label=f"#{rec.id}: skip {dates}", row=row))
        view.add_item(StopRepeatingButton(rec.id, label=f"Stop #{rec.id}", row=row))
    return view


def panel_view(poll: Poll, response: UserResponse) -> discord.ui.View:
    """A user's personal panel: one default picker plus one multi-select per status."""
    view = discord.ui.View(timeout=None)
    view.add_item(DefaultSelect(poll.id, response.default))
    for status in STATUS_ORDER:
        selected = {d for d, s in response.overrides.items() if s == status}
        view.add_item(DaySelect(poll.id, status, poll.days, selected))
    return view


async def _send_panel(interaction: discord.Interaction, poll: Poll, *, edit: bool) -> None:
    bot: ScheduleBot = interaction.client  # type: ignore[assignment]
    response = bot.db.response(poll.id, interaction.user.id)
    kwargs = dict(content=panel_text(poll, response), view=panel_view(poll, response))
    if edit:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


class StatusButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:status:(?P<poll>\d+):(?P<status>\d)"):
    """Sets the clicker's default for every day, then opens their panel for fine-tuning."""

    def __init__(self, poll_id: int, status: Status):
        super().__init__(
            discord.ui.Button(
                label=status.label,
                emoji=status.emoji,
                style=_BUTTON_STYLE[status],
                custom_id=f"sched:status:{poll_id}:{int(status)}",
            ),
            row=0,
        )
        self.poll_id = poll_id
        self.status = status

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]), Status(int(match["status"])))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        poll = await bot.open_poll_for(interaction, self.poll_id)
        if poll is None:
            return
        bot.db.set_default(poll.id, interaction.user.id, self.status)
        await _send_panel(interaction, poll, edit=False)
        bot.request_refresh(poll.id)


class SameAsLastButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:last:(?P<poll>\d+)"):
    """Copies the clicker's answer from their previous poll in this server, matched up by weekday."""

    def __init__(self, poll_id: int):
        super().__init__(
            discord.ui.Button(
                label="Same as last time",
                emoji="🔁",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:last:{poll_id}",
            ),
            row=1,
        )
        self.poll_id = poll_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        poll = await bot.open_poll_for(interaction, self.poll_id)
        if poll is None:
            return
        previous = bot.db.previous_answer(poll.guild_id, interaction.user.id, poll.id)
        carried = carry_over(previous[0].days, previous[1], poll.days) if previous else None
        if carried is None or not carried.has_answered:
            await interaction.response.send_message(
                "I don't have an earlier answer from you that fits these days — pick one of the status buttons instead.",
                ephemeral=True,
            )
            return
        bot.db.replace_response(poll.id, interaction.user.id, carried)
        await _send_panel(interaction, poll, edit=False)
        bot.request_refresh(poll.id)


class FineTuneButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:panel:(?P<poll>\d+)"):
    """Opens the clicker's panel without changing anything."""

    def __init__(self, poll_id: int):
        super().__init__(
            discord.ui.Button(
                label="Fine-tune days",
                emoji="🗓️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:panel:{poll_id}",
            ),
            row=1,
        )
        self.poll_id = poll_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        poll = await bot.open_poll_for(interaction, self.poll_id)
        if poll is not None:
            await _send_panel(interaction, poll, edit=False)


class CloseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:close:(?P<poll>\d+)"):
    def __init__(self, poll_id: int):
        super().__init__(
            discord.ui.Button(
                label="Close poll",
                emoji="🔒",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:close:{poll_id}",
            ),
            row=1,
        )
        self.poll_id = poll_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        poll = await bot.open_poll_for(interaction, self.poll_id)
        if poll is None:
            return
        if interaction.user.id != poll.creator_id and not interaction.permissions.manage_messages:
            await interaction.response.send_message(
                f"Only <@{poll.creator_id}> (or a moderator) can close this poll.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await bot.close_poll(poll)


class DefaultSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"sched:default:(?P<poll>\d+)"):
    def __init__(self, poll_id: int, current: Status | None = None):
        super().__init__(
            discord.ui.Select(
                custom_id=f"sched:default:{poll_id}",
                placeholder="My default for every day…",
                options=[
                    discord.SelectOption(
                        label=f"Default: {s.label}", value=str(int(s)), emoji=s.emoji, default=s == current
                    )
                    for s in STATUS_ORDER
                ],
            ),
            row=0,
        )
        self.poll_id = poll_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        poll = await bot.open_poll_for(interaction, self.poll_id)
        if poll is None:
            return
        values = interaction.data.get("values", [])  # type: ignore[union-attr]
        if values:
            bot.db.set_default(poll.id, interaction.user.id, Status(int(values[0])))
        await _send_panel(interaction, poll, edit=True)
        bot.request_refresh(poll.id)


class DaySelect(discord.ui.DynamicItem[discord.ui.Select], template=r"sched:days:(?P<poll>\d+):(?P<status>\d)"):
    """Multi-select of the days on which the user's answer is ``status`` instead of their default."""

    def __init__(
        self,
        poll_id: int,
        status: Status,
        days: list[date] | None = None,
        selected: set[date] | None = None,
    ):
        days = days or []
        selected = selected or set()
        super().__init__(
            discord.ui.Select(
                custom_id=f"sched:days:{poll_id}:{int(status)}",
                placeholder=_DAY_SELECT_PLACEHOLDER[status],
                min_values=0,
                max_values=max(len(days), 1),
                options=[
                    discord.SelectOption(label=day_label(d), value=d.isoformat(), default=d in selected)
                    for d in days
                ],
            ),
            row=1 + STATUS_ORDER.index(status),
        )
        self.poll_id = poll_id
        self.status = status

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]), Status(int(match["status"])))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        poll = await bot.open_poll_for(interaction, self.poll_id)
        if poll is None:
            return
        valid = set(poll.days)
        picked = [date.fromisoformat(v) for v in interaction.data.get("values", [])]  # type: ignore[union-attr]
        bot.db.set_days_for_status(poll.id, interaction.user.id, self.status, [d for d in picked if d in valid])
        await _send_panel(interaction, poll, edit=True)
        bot.request_refresh(poll.id)


class StopRepeatingButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:rstop:(?P<rec>\d+)"):
    """Stops a recurring poll from being posted again. Polls already posted stay open."""

    def __init__(self, recurrence_id: int, label: str | None = None, row: int | None = None):
        super().__init__(
            discord.ui.Button(
                label=label or "Stop repeating",
                emoji="🛑",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:rstop:{recurrence_id}",
            ),
            row=row,
        )
        self.recurrence_id = recurrence_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["rec"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        await bot.stop_recurrence(interaction, self.recurrence_id)


class SkipPollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:pskip:(?P<poll>\d+)"):
    """Closes one repeating poll as "skipped this time"; the next one still comes as usual."""

    def __init__(self, poll_id: int):
        super().__init__(
            discord.ui.Button(
                label="Skip this time",
                emoji="⏭️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:pskip:{poll_id}",
            ),
            row=1,
        )
        self.poll_id = poll_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        await bot.skip_poll(interaction, self.poll_id)


class SkipNextButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sched:rskip:(?P<rec>\d+)"):
    """Skips a recurrence's next period before its poll goes out."""

    def __init__(self, recurrence_id: int, label: str | None = None, row: int | None = None):
        super().__init__(
            discord.ui.Button(
                label=label or "Skip next",
                emoji="⏭️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:rskip:{recurrence_id}",
            ),
            row=row,
        )
        self.recurrence_id = recurrence_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["rec"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        await bot.skip_next(interaction, self.recurrence_id)


class UndoSkipButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"sched:runskip:(?P<rec>\d+):(?P<start>\d{8})"
):
    """Puts back a period skipped with SkipNextButton, if its poll hasn't been needed yet."""

    def __init__(self, recurrence_id: int, skipped_start: date):
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:runskip:{recurrence_id}:{skipped_start:%Y%m%d}",
            )
        )
        self.recurrence_id = recurrence_id
        self.skipped_start = skipped_start

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        s = match["start"]
        return cls(int(match["rec"]), date(int(s[:4]), int(s[4:6]), int(s[6:])))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ScheduleBot = interaction.client  # type: ignore[assignment]
        await bot.undo_skip(interaction, self.recurrence_id, self.skipped_start)
