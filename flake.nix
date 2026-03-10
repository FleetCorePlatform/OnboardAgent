{
    inputs = {
        nixpkgs.url = "github:nixos/nixpkgs/nixos-24.11";
    };

    outputs = { self, nixpkgs, ...}:
        let
            system = "x86_64-linux";
            pkgs = nixpkgs.legacyPackages.${system};

            inherit (pkgs) lib;

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
                mesa
                glib-networking
                libpulseaudio
                libnice
                gcc
            ];

            gstreamerPkgs = with pkgs.gst_all_1; [
              gstreamer
              gst-plugins-base
              gst-plugins-good
              gst-plugins-bad
              gst-plugins-ugly
              gst-libav
            ];
        in {
            devShells.${system}.default = pkgs.mkShell {
                buildInputs = packages ++ gstreamerPkgs;
                nativeBuildInputs = with pkgs; [ pkg-config wrapGAppsHook4 xdotool ];

                shellHook = ''
                  export LD_LIBRARY_PATH=${lib.makeLibraryPath (packages ++ gstreamerPkgs)}:$LD_LIBRARY_PATH

                  export GIO_MODULE_DIR="${pkgs.glib-networking}/lib/gio/modules/"

                  export GST_PLUGIN_SYSTEM_PATH_1_0=${lib.concatMapStringsSep ":" (pkg: "${pkg}/lib/gstreamer-1.0") (with pkgs.gst_all_1; [
                    gstreamer.out
                    gst-plugins-base
                    gst-plugins-good
                    gst-plugins-bad
                    gst-plugins-ugly
                    gst-libav
                  ] ++ [ pkgs.libnice ])}

                  export GST_PLUGIN_PATH=$GST_PLUGIN_SYSTEM_PATH_1_0
                '';
            };
        };
}