"""Building the embeds and text the bot shows."""

from __future__ import annotations

from datetime import date, datetime, time

import discord

from .models import NO_ANSWER_EMOJI, STATUS_ORDER, Poll, Recurrence, Status, UserResponse
from .recurrence import period_label, repeat_label
from .scoring import DaySummary, rank, summarize

OPEN_COLOR = discord.Color.blurple()
CLOSED_COLOR = discord.Color.green()
DESCRIPTION_LIMIT = 3000  # keeps the whole embed under Discord's 6000-char cap
FIELD_LIMIT = 1024


def day_label(day: date) -> str:
    return day.strftime("%a %b ") + str(day.day)


def range_label(start: date, end: date) -> str:
    if start == end:
        return day_label(start)
    return f"{day_label(start)} – {day_label(end)}"


def time_label(t: time) -> str:
    """18:00 -> '6pm', 09:30 -> '9:30am'."""
    minutes = f":{t.minute:02d}" if t.minute else ""
    return f"{t.hour % 12 or 12}{minutes}{'am' if t.hour < 12 else 'pm'}"


def nag_label(seconds: int, nag_time: time | None = None) -> str:
    if not seconds:
        return "off"
    if seconds % 86400 == 0:
        days = seconds // 86400
        label = "daily" if days == 1 else f"every {days} days"
        return f"{label} at {time_label(nag_time)}" if nag_time else label
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "hourly" if hours == 1 else f"every {hours}h"
    return f"every {seconds // 60}m"


def mentions(user_ids, limit: int | None = None) -> str:
    """Space-separated mentions; with ``limit``, extras become "and N more" (message content caps at 2000 chars)."""
    ids = sorted(user_ids)
    text = " ".join(f"<@{uid}>" for uid in ids[:limit])
    if limit is not None and len(ids) > limit:
        text += f" and {len(ids) - limit} more"
    return text


def summary_line(s: DaySummary) -> str:
    """e.g. "⭐ 2 · ✅ 3 · 5/6 can make it"."""
    bits = [f"{st.emoji} {s.count(st)}" for st in (Status.PREFERRED, Status.AVAILABLE, Status.TENTATIVE) if s.count(st)]
    if s.count(Status.UNAVAILABLE):
        bits.append(f"{Status.UNAVAILABLE.emoji} {s.count(Status.UNAVAILABLE)}")
    return f"{s.can_make_it}/{s.total} can make it ({' · '.join(bits)})"


def breakdown_lines(summaries: list[DaySummary], best: date | None) -> list[str]:
    lines = []
    for s in summaries:
        dots = "".join(st.emoji * s.count(st) for st in STATUS_ORDER) + NO_ANSWER_EMOJI * len(s.no_answer)
        line = f"`{day_label(s.day):<10}` {dots or '—'}"
        if s.day == best:
            line += " 🏆"
        notes = []
        if s.by_status[Status.UNAVAILABLE]:
            notes.append(f"out: {mentions(s.by_status[Status.UNAVAILABLE])}")
        if s.by_status[Status.TENTATIVE]:
            notes.append(f"maybe: {mentions(s.by_status[Status.TENTATIVE])}")
        if notes:
            line += " — " + " · ".join(notes)
        lines.append(line)
    return lines


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def when_label(epoch: float, tz) -> str:
    """e.g. 'Thu Oct 29 at 6pm'."""
    moment = datetime.fromtimestamp(epoch, tz)
    return f"{day_label(moment.date())} at {time_label(moment.time())}"


def recurrence_line(rec: Recurrence, next_post: float, tz) -> str:
    who = mentions(rec.user_ids) + (" " if rec.user_ids and rec.role_ids else "")
    who += " ".join(f"<@&{rid}>" for rid in sorted(rec.role_ids))
    return (
        f"**#{rec.id} {rec.title}**: {period_label(rec.period_start, rec.period_days)}, "
        f"{repeat_label(rec.interval_weeks)}, in <#{rec.channel_id}> for {who or 'everyone in the channel'}. "
        f"Next poll {when_label(next_post, tz)}."
    )


def poll_embed(poll: Poll, responses: dict[int, UserResponse], repeat_note: str | None = None) -> discord.Embed:
    summaries = summarize(poll.days, responses)
    ranked = rank(summaries)
    best = ranked[0].day if ranked else None
    answered = {uid for uid, r in responses.items() if r.has_answered}
    waiting = set(responses) - answered

    title = f"📅 {poll.title}" if not poll.closed else f"🔒 {poll.title} (closed)"
    header = f"**{range_label(poll.start, poll.end)}**\n"
    if not poll.closed:
        header += "Click your usual availability below, then fine-tune any days that differ.\n"
    header += "\n"
    body = "\n".join(breakdown_lines(summaries, best)) if answered else "_No responses yet._"
    embed = discord.Embed(
        title=title,
        description=_clip(header + body, DESCRIPTION_LIMIT),
        color=CLOSED_COLOR if poll.closed else OPEN_COLOR,
    )

    if ranked:
        top = [f"**{i}. {day_label(s.day)}** — {summary_line(s)}" for i, s in enumerate(ranked[:3], 1)]
        embed.add_field(name="🏆 Best options", value=_clip("\n".join(top), FIELD_LIMIT), inline=False)

    embed.add_field(
        name=f"Responded ({len(answered)}/{len(responses)})",
        value=_clip(mentions(answered) or "nobody yet", FIELD_LIMIT),
        inline=True,
    )
    if waiting:
        embed.add_field(name="Waiting on", value=_clip(mentions(waiting), FIELD_LIMIT), inline=True)

    legend = "  ".join(f"{s.emoji} {s.label}" for s in STATUS_ORDER) + f"  {NO_ANSWER_EMOJI} No answer"
    if not poll.closed:
        legend += f"  •  Reminders: {nag_label(poll.nag_interval, poll.nag_time)}"
        if repeat_note:
            legend += f"  •  {repeat_note}"
    embed.set_footer(text=legend)
    return embed


def panel_text(poll: Poll, response: UserResponse) -> str:
    """The message shown above a user's personal (ephemeral) availability panel."""
    if response.default is None:
        default = "not set (pick one below — it fills in every day you don't override)"
    else:
        default = f"{response.default.emoji} {response.default.label}"
    days = []
    for day in poll.days:
        status = response.status_on(day)
        mark = status.emoji if status is not None else NO_ANSWER_EMOJI
        days.append(f"`{day_label(day):<10}` {mark}")
    return (
        f"**Your availability for {poll.title}** ({range_label(poll.start, poll.end)})\n"
        f"Default: {default}\n"
        + "\n".join(days)
        + "\n-# Changes save automatically. Use the day menus below for days that differ from your default."
    )


def closing_text(poll: Poll, responses: dict[int, UserResponse]) -> str:
    ranked = rank(summarize(poll.days, responses))
    if not ranked:
        return f"🔒 **{poll.title}** is closed — nobody could make any of the days. 😢"
    best = ranked[0]
    return f"🔒 **{poll.title}** is closed. Best option: **{day_label(best.day)}** — {summary_line(best)}."
