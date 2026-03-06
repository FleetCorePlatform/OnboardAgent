{
    inputs = {
        nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    };

    outputs = { self, nixpkgs, ...}:
        let
            system = "x86_64-linux";
            pkgs = nixpkgs.legacyPackages.${system};
        in {
            devShells.${system}.default = pkgs.mkShell {
                packages = with pkgs; [
                    uv
                    python313
                    black
                    just
                    cairo
                    glib
                    gobject-introspection
                    python3Packages.pygobject3
                    pkg-config
                    gcc
                ];
            };
        };
}