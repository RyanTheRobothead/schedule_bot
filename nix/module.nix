self:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.schedule-bot;
in
{
  options.services.schedule-bot = {
    enable = lib.mkEnableOption "the schedule_bot Discord bot";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The schedule-bot package to run.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.path;
      example = "/run/secrets/schedule-bot.env";
      description = ''
        File containing `DISCORD_TOKEN=...` (and optionally other SCHEDULE_BOT_* settings).
        Kept out of the Nix store.
      '';
    };

    timezone = lib.mkOption {
      type = lib.types.str;
      default = config.time.timeZone or "UTC";
      defaultText = lib.literalExpression ''config.time.timeZone or "UTC"'';
      example = "America/Chicago";
      description = "Timezone used to decide what \"today\" and \"next week\" mean.";
    };

    nagTime = lib.mkOption {
      type = lib.types.str;
      default = "18:00";
      example = "9am";
      description = ''
        Time of day that daily (or every-N-days) reminders go out, in `timezone`.
        `"off"` makes reminders relative to when each poll was posted.
      '';
    };

    dateOrder = lib.mkOption {
      type = lib.types.enum [
        "MDY"
        "DMY"
        "YMD"
      ];
      default = "MDY";
      description = "How to read ambiguous numeric dates like 03/04.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.schedule-bot = {
      description = "schedule_bot Discord bot";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      environment = {
        SCHEDULE_BOT_DB = "/var/lib/schedule-bot/schedule_bot.db";
        SCHEDULE_BOT_TZ = cfg.timezone;
        SCHEDULE_BOT_DATE_ORDER = cfg.dateOrder;
        SCHEDULE_BOT_NAG_TIME = cfg.nagTime;
      };
      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        EnvironmentFile = cfg.environmentFile;
        DynamicUser = true;
        StateDirectory = "schedule-bot";
        Restart = "on-failure";
        RestartSec = 10;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
      };
    };
  };
}
