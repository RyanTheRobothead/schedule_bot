{
  description = "Discord bot for scheduling TTRPG / game nights";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.python3Packages.callPackage ./nix/package.nix { };
      });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/schedule-bot";
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.discordpy
              ps.aiohttp
              ps.dateparser
              ps.tzdata
              ps.pytest
            ]))
          ];
        };
      });

      checks = forAllSystems (pkgs: {
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });

      nixosModules.default = import ./nix/module.nix self;
    };
}
