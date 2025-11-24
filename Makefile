.PHONY: de format build run

default: run

dep:
	/bin/bash -c ./scripts/system_dependencies.sh

model:
	@if [ ! -f "./models/yolov8n.pt" ]; then \
		mkdir -p ./models/; \
		wget https://huggingface.co/Ultralytics/YOLOv8/resolve/main/yolov8n.pt -O models/yolov8n.pt; \
	else \
		echo "Model already present in models/"; \
	fi

venv:
	@if [ ! -d "./.venv" ]; then uv venv; fi
	uv sync

de:
ifeq ($(v),classic)
	$(MAKE) venv model
	/bin/bash ./sim/gazebo_classic_dev_env.sh
else ifeq ($(v),pegasus)
	$(MAKE) venv model
	/bin/bash ./sim/pegasus_dev_env.sh
else
	$(MAKE) venv model
	/bin/bash ./sim/gazebo_dev_env.sh
endif

format:
	black .

run: format
	uv run -m src.main .config.env

test:
ifeq ($(all),1)
	uv run pytest tests/
else
	uv run pytest tests/unit/
endif
