from __future__ import annotations

import logging

from .bot import ScheduleBot
from .config import Config


def main() -> None:
    config = Config.from_env()
    bot = ScheduleBot(config)
    bot.run(config.token, log_level=logging.INFO)


if __name__ == "__main__":
    main()
