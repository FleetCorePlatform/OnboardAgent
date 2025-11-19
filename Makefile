.PHONY: de format build run

default: run

venv:
	@if [ ! -d "./.venv" ]; then uv venv; fi
	uv sync

de:
ifeq ($(classic),1)
	$(MAKE) venv
	/bin/bash ./sim/gazebo_classic_dev_env.sh
else ifeq ($(pegasus),1)
	$(MAKE) venv
	/bin/bash ./sim/pegasus_dev_env.sh
else
	$(MAKE) venv
	/bin/bash ./sim/gazebo_dev_env.sh
endif

format:
	black .

run: format
	uv run -m src.main

test:
ifeq ($(all),1)
	uv run pytest tests/
else
	uv run pytest tests/unit/
endif