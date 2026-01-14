.PHONY: help run stop dev build test test-unit test-integration test-all format lint clean install uninstall setup-backend setup-frontend setup-waybar setup-autostart setup-hotkey

help:
	@echo "Oflow - Voice Dictation for Hyprland/Wayland"
	@echo ""
	@echo "Available targets:"
	@echo "  make install         - Full install: build, install binary, setup Waybar & autostart"
	@echo "  make uninstall       - Remove oflow binary, Waybar config, and autostart"
	@echo "  make dev             - Run in development mode (hot reload)"
	@echo "  make build           - Build release binary only"
	@echo "  make run             - Start the backend server only"
	@echo "  make stop            - Stop all oflow processes"
	@echo "  make test            - Run unit tests (fast, no API needed)"
	@echo "  make test-integration - Run integration tests (requires API keys)"
	@echo "  make test-all        - Run all tests"
	@echo "  make format          - Format code with ruff"
	@echo "  make lint            - Lint code with ruff"
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

install: build setup-hotkey
	@echo "Installing oflow..."
	@mkdir -p ~/.local/bin
	@cp oflow-ui/src-tauri/target/release/oflow-ui ~/.local/bin/oflow
	@chmod +x ~/.local/bin/oflow
	@# Create toggle script for Waybar
	@echo '#!/bin/bash' > ~/.local/bin/oflow-toggle
	@echo 'LOCKFILE="/tmp/oflow-toggle.lock"' >> ~/.local/bin/oflow-toggle
	@echo 'exec 200>"$$LOCKFILE"' >> ~/.local/bin/oflow-toggle
	@echo 'flock -n 200 || exit 0' >> ~/.local/bin/oflow-toggle
	@echo 'ADDR=$$(hyprctl clients -j | jq -r ".[] | select(.class == \"oflow\") | .address" | head -1)' >> ~/.local/bin/oflow-toggle
	@echo 'if [ -n "$$ADDR" ] && [ "$$ADDR" != "null" ]; then' >> ~/.local/bin/oflow-toggle
	@echo '    FOCUSED=$$(hyprctl activewindow -j | jq -r ".address" 2>/dev/null)' >> ~/.local/bin/oflow-toggle
	@echo '    if [ "$$ADDR" = "$$FOCUSED" ]; then' >> ~/.local/bin/oflow-toggle
	@echo '        hyprctl dispatch movetoworkspacesilent special:hidden,address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '    else' >> ~/.local/bin/oflow-toggle
	@echo '        hyprctl dispatch movetoworkspacesilent e+0,address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '        hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '    fi' >> ~/.local/bin/oflow-toggle
	@echo 'else' >> ~/.local/bin/oflow-toggle
	@echo '    pgrep -x oflow > /dev/null && pkill -x oflow && sleep 0.2' >> ~/.local/bin/oflow-toggle
	@echo '    ~/.local/bin/oflow &' >> ~/.local/bin/oflow-toggle
	@echo 'fi' >> ~/.local/bin/oflow-toggle
	@chmod +x ~/.local/bin/oflow-toggle
	@$(MAKE) setup-waybar
	@$(MAKE) setup-autostart
	@echo ""
	@echo "Installation complete!"
	@echo "  - Binary: ~/.local/bin/oflow"
	@echo "  - Click the ○ icon in Waybar to open settings"
	@echo "  - Hold Super+B to record, release to transcribe"
	@echo ""
	@echo "Starting oflow..."
	@~/.local/bin/oflow-toggle

setup-waybar:
	@echo "Configuring Waybar..."
	@if [ -f ~/.config/waybar/config.jsonc ]; then \
		if ! grep -q '"custom/oflow"' ~/.config/waybar/config.jsonc; then \
			echo "Adding oflow module to Waybar config..."; \
			echo "Please add 'custom/oflow' to your modules and add this config:"; \
			echo '  "custom/oflow": {'; \
			echo '    "exec": "cat $$XDG_RUNTIME_DIR/oflow/state 2>/dev/null || echo '\''{"text":"○","class":"idle","tooltip":"oflow not running"}'\''",'; \
			echo '    "return-type": "json",'; \
			echo '    "interval": 1,'; \
			echo '    "format": "{}",'; \
			echo '    "tooltip": true,'; \
			echo '    "on-click": "~/.local/bin/oflow-toggle"'; \
			echo '  }'; \
		else \
			echo "Waybar oflow module already configured"; \
		fi \
	else \
		echo "Waybar config not found at ~/.config/waybar/config.jsonc"; \
	fi

setup-autostart:
	@echo "Setting up autostart..."
	@mkdir -p ~/.config/autostart
	@echo "[Desktop Entry]" > ~/.config/autostart/oflow.desktop
	@echo "Type=Application" >> ~/.config/autostart/oflow.desktop
	@echo "Name=oflow" >> ~/.config/autostart/oflow.desktop
	@echo "Comment=Voice dictation for Hyprland" >> ~/.config/autostart/oflow.desktop
	@echo "Exec=$(HOME)/.local/bin/oflow" >> ~/.config/autostart/oflow.desktop
	@echo "Icon=audio-input-microphone" >> ~/.config/autostart/oflow.desktop
	@echo "Terminal=false" >> ~/.config/autostart/oflow.desktop
	@echo "Categories=Utility;AudioVideo;" >> ~/.config/autostart/oflow.desktop
	@echo "StartupNotify=false" >> ~/.config/autostart/oflow.desktop
	@echo "Autostart entry created"

setup-hotkey:
	@echo "Setting up Super+B hotkey..."
	@if [ -f ~/.config/hypr/bindings.conf ]; then \
		if grep -q "# Oflow voice dictation" ~/.config/hypr/bindings.conf; then \
			sed -i '/# Oflow voice dictation/,/^$$/d' ~/.config/hypr/bindings.conf; \
		fi; \
		echo "" >> ~/.config/hypr/bindings.conf; \
		echo "# Oflow voice dictation (push-to-talk: hold Super+B)" >> ~/.config/hypr/bindings.conf; \
		echo "bind = SUPER, B, exec, $(PWD)/.venv/bin/python $(PWD)/oflow.py start" >> ~/.config/hypr/bindings.conf; \
		echo "bindr = SUPER, B, exec, $(PWD)/.venv/bin/python $(PWD)/oflow.py stop" >> ~/.config/hypr/bindings.conf; \
		hyprctl reload 2>/dev/null || true; \
		echo "Hotkey configured: Super+B"; \
	else \
		echo "Hyprland bindings.conf not found"; \
	fi

uninstall:
	@echo "Uninstalling oflow..."
	@rm -f ~/.local/bin/oflow ~/.local/bin/oflow-toggle
	@rm -f ~/.config/autostart/oflow.desktop
	@if [ -f ~/.config/hypr/bindings.conf ]; then \
		sed -i '/# Oflow voice dictation/,/bindr.*oflow/d' ~/.config/hypr/bindings.conf; \
		hyprctl reload 2>/dev/null || true; \
	fi
	@$(MAKE) stop
	@echo "Uninstall complete"

clean:
	@echo "Cleaning cache files..."
	@rm -rf __pycache__ .pytest_cache .ruff_cache .coverage htmlcov
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Clean complete"
