.PHONY: local install dev test lint logs logs-trade logs-error clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

local: $(VENV)
	$(PYTHON) main.py

install: $(VENV)
	$(PIP) install -e .

dev: $(VENV)
	$(PIP) install -e ".[dev]"

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

test: $(VENV)
	$(PYTHON) -m pytest tests/ -q

lint: $(VENV)
	$(PYTHON) -m py_compile main.py
	$(PYTHON) -c "import compileall; compileall.compile_dir('app', quiet=1)"

logs:
	tail -f logs/app.log

logs-trade:
	tail -f logs/trading.log

logs-error:
	tail -f logs/error.log

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache data/*.db logs/*.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
