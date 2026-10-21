# schedule_bot

A Discord bot for scheduling semi-regular game nights without anyone leaving Discord.

```
@schedule_bot                                    # next Monday–Sunday, everyone in the channel
@schedule_bot fri-sun "One-shot" @Players        # just the Players role, custom title
@schedule_bot oct 30 - nov 8 @alice @bob nag=12h
@schedule_bot next 2 weeks no nag
@schedule_bot help
```

## How answering works

The poll message has four buttons: **⭐ Preferred · ✅ Available · ❔ Tentative · ❌ Unavailable**.

1. Click the one that describes your week as a whole. That's saved as your *default* for every day,
   and a private panel pops up showing your answers.
2. The panel has one "…on which days?" menu per status. Pick the days that differ from your default.
   Everything saves as you click; there's no submit button.

"Available all week, except Tuesday unavailable and Friday preferred" is three clicks:
**✅ Available** → *❌ Unavailable on… Tue* → *⭐ Preferred on… Fri*.

The 🗓️ button reopens your panel to check or change your answers. Anyone can respond, even if
they weren't invited; they're added to the poll.

The poll message is kept up to date with a per-day breakdown (who's in, who's tentative, who's out),
the top three days, and who hasn't answered yet. Ranking: fewest people unavailable first, then
fewest maybes, then the most ⭐, then the earliest date. So a day everyone can make beats a day four
people love and one person can't do.

**Reminders:** once a day by default, the bot @-mentions anyone invited who hasn't answered.
Change it per poll with `nag=12h`, `nag=2d`, `nag=off` / `no nag`.

**Closing:** the poll's creator (or anyone with Manage Messages) can click 🔒 *Close poll*, which
freezes it and announces the best day. Polls close themselves once their last day has passed.

### Invocation reference

| Part | Examples | Default |
|---|---|---|
| Dates | `next week`, `this week`, `weekend`, `next weekend`, `next 10 days`, `2 weeks`, `friday`, `next fri`, `fri-sun`, `oct 30 - nov 3`, `Oct 25-31`, `11/1-11/7`, `2026-11-01..2026-11-07` | next Mon–Sun |
| People | `@user` and/or `@role` mentions | everyone who can see the channel (thread members, in a thread) |
| Title | `"Anything in double quotes"` | `Game night` |
| Reminders | `nag=12h`, `nag=daily`, `nag=off`, `no nag` | `SCHEDULE_BOT_NAG` (daily) |

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
3. **OAuth2 → URL Generator**: scope `bot`; permissions **View Channels**, **Send Messages**,
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
- `schedule_bot/db.py`: SQLite storage
- `schedule_bot/render.py`: embeds and panel text
- `schedule_bot/views.py`: buttons and menus (`DynamicItem`s, so they keep working across restarts)
- `schedule_bot/bot.py`: the client: invocations, live message updates, reminders, closing
