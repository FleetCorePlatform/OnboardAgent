#!/bin/bash

export CONTAINER_NAME="px4-gazebo"

setup() {
	(cd ./sim/gazebo && docker build -t fleetcoreagent/px4-dev-gazebo-jammy:latest -f Dockerfile.Gazebo .)
}

run() {
  xhost +local:docker

  HOST_VIDEO_GID=$(getent group video | cut -d: -f3)
  HOST_RENDER_GID=$(getent group render | cut -d: -f3)

  docker run --rm -it --privileged \
    --env=LOCAL_USER_ID="1002" \
    -v "/tmp/.X11-unix:/tmp/.X11-unix:ro" \
    -v "/run/user/$(id -u):/run/user/$(id -u):ro" \
    --group-add ${HOST_VIDEO_GID} \
    --group-add ${HOST_RENDER_GID} \
    --gpus all \
    -e DISPLAY=$DISPLAY \
    -e XDG_RUNTIME_DIR="/run/user/$(id -u)" \
    -e NVIDIA_VISIBLE_DEVICES=all \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
    --network host \
    --device=/dev/dri:/dev/dri \
    --name=$CONTAINER_NAME \
    fleetcoreagent/px4-dev-gazebo-jammy:latest
}

if ! command -v nvidia-smi; then
  echo "No Nvidia GPU detected, exiting.."
  exit 1
fi

if [ -z "$(docker images -q fleetcoreagent/px4-dev-gazebo-jammy:latest)" ]; then
	echo "Image not found, building..."
	setup
else
	echo "PX4 Autopilot already present, starting development environment..."
	run

	exit 0
fi

echo "Starting Gazebo container..."
run
