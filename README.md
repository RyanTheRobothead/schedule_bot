# schedule_bot

A Discord bot for scheduling semi-regular game nights without anyone leaving Discord.

```
@schedule_bot                                    # next Monday–Sunday, everyone in the channel
@schedule_bot fri-sun "One-shot" @Players        # just the Players role, custom title
@schedule_bot oct 30 - nov 8 @alice @bob nag=12h
@schedule_bot next 2 weeks no nag
@schedule_bot help
```

Or use the `/schedule` slash command, which has the same options as fields (`dates`, `title`,
`invite`, `nag`) and shows hints as you type.

## How answering works

The poll message has four buttons: **⭐ Preferred · ✅ Available · ❔ Tentative · ❌ Unavailable**.

1. Click the one that describes your week as a whole. That's saved as your *default* for every day,
   and a private panel pops up showing your answers.
2. The panel has one "…on which days?" menu per status. Pick the days that differ from your default.
   Everything saves as you click; there's no submit button.

"Available all week, except Tuesday unavailable and Friday preferred" is three clicks:
**✅ Available** → *❌ Unavailable on… Tue* → *⭐ Preferred on… Fri*.

**🔁 Same as last time** copies your answer from the previous poll in this server, matched up by
weekday: if last time you were available except Tuesdays, you're available except Tuesdays again.
It's always a click you make; the bot never answers for you, so reminders still reach anyone who
hasn't clicked anything.

The 🗓️ button reopens your panel to check or change your answers. Anyone can respond, even if
they weren't invited; they're added to the poll.

The poll message is kept up to date with a per-day breakdown (who's in, who's tentative, who's out),
the top three days, and who hasn't answered yet. Ranking: fewest people unavailable first, then
fewest maybes, then the most ⭐, then the earliest date. So a day everyone can make beats a day four
people love and one person can't do.

**Reminders:** once a day by default, at 6pm (`SCHEDULE_BOT_NAG_TIME`), the bot @-mentions anyone
invited who hasn't answered. The first one waits at least 6 hours after the poll goes up. Change it
per poll with `nag=12h`, `nag=2d`, `nag=9am` (daily at 9am), `nag=2d@7pm`, `nag=off` / `no nag`.
Whole-day intervals go out at the set time of day; shorter ones (`12h`) count from the last reminder.

**Closing:** the poll's creator (or anyone with Manage Messages) can click 🔒 *Close poll*, which
freezes it and announces the best day. Polls close themselves once their last day has passed.

## Repeating polls

Add a repeat phrase and the bot posts a fresh poll for every period on its own:

```
@schedule_bot every week                          # each Mon–Sun, posted the Thursday before at 6pm
@schedule_bot every friday "Board games" @Crew    # each Friday
@schedule_bot every 2 weeks fri-sun lead=1d@9am   # every other Fri–Sun, posted Thursday 9am
@schedule_bot every other week next 2 weeks       # back-to-back fortnights
@schedule_bot recurring                           # list them (or /recurring)
```

- **Cadence** is weekly-based: `every week` / `weekly`, `every N weeks`, `every other week` /
  `biweekly`, `every <weekday>`, `every weekend`. The dates work like a normal poll's and shift
  forward by the cadence each time. (Monthly isn't supported; polls are capped at 25 days.)
- **`lead=`** says how far ahead each poll goes out: `lead=4d` (default, at 6pm), `lead=1w`,
  `lead=2d@9am`, `lead=0d@9am` (the morning of the first day). It's capped at the cadence so polls
  don't pile up.
- If you set one up when the first poll is already due (or the period is under way), that poll goes
  out right away. If the bot was down, finished periods are skipped and one under way still gets
  posted.
- Invited people and roles are looked up fresh each time, so role membership changes carry over. If
  none of them are left, the repeat stops itself and says so.
- Reminders follow the usual rules for each poll. A poll posted at 6pm won't nag the same evening,
  because the first reminder waits at least 6 hours.
- **🛑 Stop repeating** is on the confirmation, on each repeating poll, and in the `/recurring` list.
  The creator or anyone with Manage Messages can use it. Polls already posted stay open.

With `/schedule`, pick a `repeat` (every 1–4 weeks) and optionally a `lead`.

Posts go out within about 5 minutes of the set time.

### Invocation reference

| Part | Examples | Default |
|---|---|---|
| Dates | `next week`, `this week`, `weekend`, `next weekend`, `next 10 days`, `2 weeks`, `friday`, `next fri`, `fri-sun`, `oct 30 - nov 3`, `Oct 25-31`, `11/1-11/7`, `2026-11-01..2026-11-07` | next Mon–Sun |
| People | `@user` and/or `@role` mentions | everyone who can see the channel (thread members, in a thread) |
| Title | `"Anything in double quotes"` | `Game night` |
| Repeat | `every week`, `every 2 weeks`, `every other week`, `every friday`, `every weekend` | one-off poll |
| Lead time (repeating) | `lead=4d`, `lead=1w`, `lead=2d@9am` | `lead=4d@6pm` |
| Reminders | `nag=12h`, `nag=daily`, `nag=9am`, `nag=2d@7pm`, `nag=off`, `no nag` | `SCHEDULE_BOT_NAG` (daily) at `SCHEDULE_BOT_NAG_TIME` (6pm) |

Ranges are capped at 25 days, since Discord select menus hold at most 25 options. Days that have
already passed are dropped from the start of a range.

## Setup

### 1. Create the Discord application

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab: copy the **token**. Under **Privileged Gateway Intents**, turn on both:
   - **Server Members Intent**: needed to expand `@role` mentions and to work out who can see a channel.
     Without it the bot only sees whoever happens to be cached, and quietly invites too few people.
   - **Message Content Intent**: needed when someone picks the bot's *role* from the `@` autocomplete
     (it has one with the same name) instead of the bot user.
3. **OAuth2 → URL Generator**: scopes `bot` and `applications.commands` (for `/schedule`); permissions **View Channels**, **Send Messages**,
   **Send Messages in Threads**, **Embed Links**, **Read Message History**. Open the generated URL to
   invite the bot to your server.

### 2. Run it

Configuration is all environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `DISCORD_TOKEN` | Bot token (required) | — |
| `SCHEDULE_BOT_TZ` | IANA timezone that decides what "today" and "next week" mean, e.g. `America/Chicago` | system timezone |
| `SCHEDULE_BOT_DATE_ORDER` | How to read `03/04`: `MDY`, `DMY` or `YMD` | `MDY` |
| `SCHEDULE_BOT_NAG` | Default reminder interval (`daily`, `12h`, `off`, …) | `daily` |
| `SCHEDULE_BOT_NAG_TIME` | Time of day for whole-day reminders (`18:00`, `9am`), or `off` to count from when the poll was posted | `18:00` |
| `SCHEDULE_BOT_HEALTH_PORT` | Serve `GET /health` here for uptime monitors (200 when connected to Discord, else 503) | off |
| `SCHEDULE_BOT_HEALTH_HOST` | Address for the health endpoint | `127.0.0.1` |
| `SCHEDULE_BOT_DB` | SQLite database path | `$XDG_STATE_HOME/schedule_bot/schedule_bot.db` |

```sh
DISCORD_TOKEN=... SCHEDULE_BOT_TZ=America/Chicago nix run
```

Without Nix: `pip install .` then run `schedule-bot`.

### NixOS

```nix
{
  inputs.schedule-bot.url = "path:/home/you/repos/schedule_bot"; # or a git URL
  outputs = { nixpkgs, schedule-bot, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        schedule-bot.nixosModules.default
        {
          services.schedule-bot = {
            enable = true;
            environmentFile = "/run/secrets/schedule-bot.env"; # DISCORD_TOKEN=...
            timezone = "America/Chicago";                       # defaults to time.timeZone
          };
        }
      ];
    };
  };
}
```

Set `services.schedule-bot.healthCheck.port` (and `.address`) to expose `/health` for a monitor like Gatus.

The service runs as a `DynamicUser` and keeps its database in `/var/lib/schedule-bot`.

## Development

```sh
nix develop          # or `direnv allow`
pytest
python -m schedule_bot
```

Nix flakes only see files tracked by git, so `git add` new files before `nix build` / `nix develop`.

Layout:

- `schedule_bot/dates.py`: date range parsing
- `schedule_bot/invocation.py`: parsing the rest of the `@schedule_bot …` message
- `schedule_bot/scoring.py`: per-day breakdown and ranking
- `schedule_bot/nagging.py`: when reminders fire
- `schedule_bot/recurrence.py`: repeating polls: periods, lead times, catching up
- `schedule_bot/db.py`: SQLite storage
- `schedule_bot/render.py`: embeds and panel text
- `schedule_bot/views.py`: buttons and menus (`DynamicItem`s, so they keep working across restarts)
- `schedule_bot/health.py`: the optional `/health` endpoint
- `schedule_bot/bot.py`: the client: mentions and `/schedule`, live message updates, reminders, closing
