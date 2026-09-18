.PHONY: setup contracts codegen test harness lint node all

PYTHON ?= python

setup:
	@bash scripts/dev_setup.sh || powershell -File scripts/dev_setup.ps1 || $(PYTHON) -c "print('Run scripts/dev_setup.sh')"

contracts:
	$(PYTHON) scripts/contract_sync.py

codegen:
	@bash scripts/codegen.sh || $(PYTHON) contracts/codegen/python/generate_pulse_models.py && $(PYTHON) contracts/codegen/rust/generate_rust_proto.py && $(PYTHON) contracts/codegen/harness-fixtures/generate_fixtures.py

test:
	pytest core/pulse_bus/tests

harness:
	pytest harness/cases

lint:
	ruff check .
	$(PYTHON) scripts/dep_guard.py

node:
	cargo check --manifest-path node_runtime/Cargo.toml
	cargo clippy --manifest-path node_runtime/Cargo.toml

all: contracts codegen lint test harness node

