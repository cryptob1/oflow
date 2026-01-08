.PHONY: help run stop test format lint clean install

help:
	@echo "OmarchyFlow - Voice Dictation for Hyprland/Wayland"
	@echo ""
	@echo "Available targets:"
	@echo "  make run      - Start the voice dictation server"
	@echo "  make stop     - Stop the voice dictation server"
	@echo "  make test     - Run test suite"
	@echo "  make format   - Format code with ruff"
	@echo "  make lint     - Lint code with ruff"
	@echo "  make install  - Run setup script"
	@echo "  make clean    - Remove cache files"

run:
	@echo "Starting OmarchyFlow..."
	@./omarchyflow &

stop:
	@echo "Stopping OmarchyFlow..."
	@pkill -f omarchyflow || true

test:
	@echo "Running tests..."
	@python tests/test_robustness.py

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
