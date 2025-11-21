#!/usr/bin/env bash

packages=(
  "gcc"
  "gobject-introspection"
  "libgirepository1.0-dev"
  "libcairo2-dev"
  "pkg-config"
  "python3-dev"
  "python3-gi"
  "build-essential"
  "gir1.2-glib-2.0"
)

printf "Do you want to continue installing the required packages for this project? [Y/n]: "
read -re install

if [[ ! $install =~ [Y|y] ]]; then
  echo "Aborting..."
  exit 1
fi

sudo apt install -y "${packages[@]}"