{
  lib,
  buildPythonApplication,
  setuptools,
  discordpy,
  aiohttp,
  dateparser,
  tzdata,
  pytestCheckHook,
}:

buildPythonApplication {
  pname = "schedule-bot";
  version = "0.2.0";
  pyproject = true;

  src = lib.cleanSource ../.;

  build-system = [ setuptools ];

  dependencies = [
    discordpy
    aiohttp
    dateparser
    tzdata
  ];

  nativeCheckInputs = [ pytestCheckHook ];

  pythonImportsCheck = [ "schedule_bot" ];

  meta = {
    description = "Discord bot for scheduling TTRPG / game nights";
    mainProgram = "schedule-bot";
  };
}
