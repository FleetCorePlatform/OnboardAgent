#!/usr/bin/env bash

export CONTAINER_NAME="px4-isaac"

setup() {
  (cd ./sim/pegasus && docker build -t fleetcoreagent/px4-dev-isaac-jammy:latest -f Dockerfile.Pegasus .)
}

run() {
  xhost +local:docker

  docker run -it \
    --runtime=nvidia \
    --gpus all \
    -e DISPLAY=$DISPLAY \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
    -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
    -v "$(pwd)/sim/pegasus/scripts:/home/sim/scripts:rw" \
    --network host \
    --name "$CONTAINER_NAME" \
    fleetcoreagent/px4-dev-isaac-jammy:latest ./scripts/simulation_1.py
}

start_existing() {
  docker start -ai "$CONTAINER_NAME"
}

if ! command -v nvidia-smi &> /dev/null; then
  echo "No Nvidia GPU detected, exiting.."
  exit 1
fi

if [ -n "$(docker container ls -af name="$CONTAINER_NAME" -q)" ]; then
  echo "Container already exists, starting it..."
  start_existing

  exit 0
fi

if [ -z "$(docker images -q fleetcoreagent/px4-dev-isaac-jammy:latest)" ]; then
  echo "Image not found, building..."
  setup
fi


echo "Starting new container..."
run
