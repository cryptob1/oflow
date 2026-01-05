# OmarchyFlow Makefile
# Simple commands for running and testing

.PHONY: help install run start stop test clean

PYTHON := .venv/bin/python
SCRIPT := omarchyflow.py

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Run setup script
	@./setup.sh

run: ## Start the voice dictation server
	@$(PYTHON) $(SCRIPT)

start: ## Send start command to running server
	@$(PYTHON) $(SCRIPT) start

stop: ## Send stop command to running server
	@$(PYTHON) $(SCRIPT) stop

toggle: ## Send toggle command to running server
	@$(PYTHON) $(SCRIPT) toggle

test: ## Run the test suite
	@$(PYTHON) test_suite.py

clean: ## Remove generated files
	@rm -rf .venv __pycache__ *.pyc /tmp/voice-dictation.sock /tmp/debug_audio.wav
	@echo "Cleaned up"

logs: ## View server logs (if using systemd)
	@journalctl --user -u omarchyflow.service -f

status: ## Check if server is running
	@if [ -S /tmp/voice-dictation.sock ]; then \
		echo "✓ Server is running"; \
	else \
		echo "✗ Server is not running"; \
	fi
