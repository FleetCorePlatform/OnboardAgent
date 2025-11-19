#!/bin/bash

export CONTAINER_NAME="px4-gazebo-classic"

setup() {
	(cd ./sim/gazebo_classic && docker build -t fleetcoreagent/px4-dev-gazebo-classic-focal:latest -f Dockerfile.Classic .)
}

run() {
  xhost +local:docker

	docker run --rm -it --privileged \
		-v /tmp/.X11-unix:/tmp/.X11-unix:ro \
		-e DISPLAY="$DISPLAY" \
		-e NVIDIA_VISIBLE_DEVICES=all \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
    --device=/dev/dri:/dev/dri \
    --gpus all \
		--network host \
		--name="$CONTAINER_NAME" \
		fleetcoreagent/px4-dev-gazebo-classic-focal:latest
}

if ! command -v nvidia-smi &> /dev/null; then
  echo "No Nvidia GPU detected, exiting.."
  exit 1
fi

if [ -z "$(docker images -q fleetcoreagent/px4-dev-gazebo-classic-focal:latest)" ]; then
	echo "Image not found, building..."
	setup
else
	echo "PX4 Autopilot already present, starting development environment..."
	run

	exit 0
fi

echo "Starting Gazebo Classic container..."
run