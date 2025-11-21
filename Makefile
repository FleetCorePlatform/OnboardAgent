.PHONY: de format build run

default: run

dep:
	/bin/bash -c ./scripts/system_dependencies.sh

venv:
	@if [ ! -d "./.venv" ]; then uv venv; fi
	uv sync

de:
ifeq ($(v),"classic")
	$(MAKE) venv
	/bin/bash ./sim/gazebo_classic_dev_env.sh
else ifeq ($(v),1)
	$(MAKE) venv
	/bin/bash ./sim/pegasus_dev_env.sh
else
	$(MAKE) venv
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
