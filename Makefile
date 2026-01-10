.PHONY: help run stop dev build test test-unit test-integration test-all format lint clean install setup-backend setup-frontend

help:
	@echo "Oflow - Voice Dictation for Hyprland/Wayland"
	@echo ""
	@echo "Available targets:"
	@echo "  make dev             - Run the desktop app (development mode)"
	@echo "  make build           - Build the desktop app for release"
	@echo "  make run             - Start the backend server only"
	@echo "  make stop            - Stop the backend server"
	@echo "  make test            - Run unit tests (fast, no API needed)"
	@echo "  make test-integration - Run integration tests (requires API keys)"
	@echo "  make test-all        - Run all tests"
	@echo "  make format          - Format code with ruff"
	@echo "  make lint            - Lint code with ruff"
	@echo "  make install         - Run setup script"
	@echo "  make clean           - Remove cache files"

setup-backend:
	@if [ ! -d ".venv" ]; then \
		echo "Creating Python environment..."; \
		uv venv; \
	fi
	@echo "Installing Python dependencies..."
	@uv pip install -q -e . --python .venv/bin/python

setup-frontend:
	@if [ ! -d "oflow-ui/node_modules" ]; then \
		echo "Installing npm dependencies..."; \
		cd oflow-ui && npm install; \
	fi

dev: setup-backend setup-frontend
	@./scripts/dev.sh

build:
	@echo "Building Oflow for release..."
	@if [ ! -d "oflow-ui/node_modules" ]; then \
		echo "Installing npm dependencies..."; \
		cd oflow-ui && npm install; \
	fi
	@cd oflow-ui && npm run tauri build

run:
	@echo "Starting Oflow backend..."
	@./oflow &

stop:
	@echo "Stopping Oflow..."
	@pkill -f "python.*oflow.py" 2>/dev/null || true
	@pkill -f "oflow-ui" 2>/dev/null || true
	@rm -f /tmp/oflow.pid /tmp/voice-dictation.sock 2>/dev/null || true
	@echo "Stopped"

test:
	@echo "Running unit tests..."
	@.venv/bin/pytest -m unit -v

test-unit: test

test-integration:
	@echo "Running integration tests (requires API keys)..."
	@.venv/bin/pytest -m integration -v

test-all:
	@echo "Running all tests..."
	@.venv/bin/pytest -v

format:
	@echo "Formatting code..."
	@ruff format .

lint:
	@echo "Linting code..."
	@ruff check .

install:
	@./setup.sh

clean:
	@echo "Cleaning cache files..."
	@rm -rf __pycache__ .pytest_cache .ruff_cache .coverage htmlcov
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Clean complete"
