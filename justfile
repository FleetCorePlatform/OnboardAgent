default: run

dep:
    /bin/bash -c ./scripts/system_dependencies.sh

model:
    #!/usr/bin/env bash
    if [ ! -f "./models/yolov8n.pt" ]; then
        mkdir -p ./models/
        wget https://huggingface.co/Ultralytics/YOLOv8/resolve/main/yolov8n.pt -O models/yolov8n.pt
    else
        echo "Model already present in models/"
    fi

venv:
    #!/usr/bin/env bash
    if [ ! -d "./.venv" ]; then
        uv venv
    fi
    uv sync

de v="default": venv model
    #!/usr/bin/env bash
    if [ "{{v}}" = "classic" ]; then
        /bin/bash ./sim/gazebo_classic_dev_env.sh
    elif [ "{{v}}" = "pegasus" ]; then
        /bin/bash ./sim/pegasus_dev_env.sh
    else
        /bin/bash ./sim/gazebo_dev_env.sh
    fi

format:
    black .

run: format
    uv run -m src.main .config.env

test all="0":
    #!/usr/bin/env bash
    if [ "{{all}}" = "1" ]; then
        uv run pytest tests/
    else
        uv run pytest tests/unit/
    fi
