.PHONY: help run stop dev build build-combined test test-unit test-integration test-all format lint clean install install-combined uninstall setup-backend setup-frontend setup-waybar setup-autostart setup-hotkey install-oflow-ctl

help:
	@echo "Oflow - Voice Dictation for Hyprland/Wayland"
	@echo ""
	@echo "Available targets:"
	@echo "  make install         - Full install: build, install binary, setup Waybar & autostart"
	@echo "  make install-combined- Install combined binary with embedded backend"
	@echo "  make uninstall       - Remove oflow binary, Waybar config, and autostart"
	@echo "  make dev             - Run in development mode (hot reload)"
	@echo "  make build           - Build release binary only"
	@echo "  make build-combined  - Build combined binary with embedded backend"
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

build-combined:
	@echo "Building combined Oflow binary with embedded backend..."
	@python3 scripts/build-combined.py
	@if [ ! -d "oflow-ui/node_modules" ]; then \
		echo "Installing npm dependencies..."; \
		cd oflow-ui && npm install; \
	fi
	@cd oflow-ui && npm run tauri build
	@echo "Combined binary build complete!"
	@echo "Binary location: oflow-ui/src-tauri/target/release/oflow-ui"

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
	@echo "Note: Backend needs to be started separately with 'make run'"

install-combined: build-combined setup-hotkey
	@echo "Installing combined oflow with embedded backend..."
	@mkdir -p ~/.local/bin
	@cp oflow-ui/src-tauri/target/release/oflow-ui ~/.local/bin/oflow
	@chmod +x ~/.local/bin/oflow
	@# Update autostart to use the new binary
	@sed -i 's|Exec=.*|Exec=$$HOME/.local/bin/oflow|' ~/.config/autostart/oflow.desktop
	@echo "Combined installation complete!"
	@echo "The backend is now embedded - no separate backend process needed!"
	@# Create toggle script for Waybar
	@echo '#!/bin/bash' > ~/.local/bin/oflow-toggle
	@echo '# oflow-toggle: Toggle oflow UI window visibility' >> ~/.local/bin/oflow-toggle
	@echo 'OFLOW_BIN="$$HOME/.local/bin/oflow"' >> ~/.local/bin/oflow-toggle
	@echo 'WIN_CLASS="oflow-ui"' >> ~/.local/bin/oflow-toggle
	@echo 'ADDR=$$(hyprctl clients -j | jq -r ".[] | select(.class == \"$$WIN_CLASS\") | .address" | head -1)' >> ~/.local/bin/oflow-toggle
	@echo 'if [ -n "$$ADDR" ] && [ "$$ADDR" != "null" ]; then' >> ~/.local/bin/oflow-toggle
	@echo '    WS=$$(hyprctl clients -j | jq -r ".[] | select(.address == \"$$ADDR\") | .workspace.name")' >> ~/.local/bin/oflow-toggle
	@echo '    if [[ "$$WS" == special:* ]]; then' >> ~/.local/bin/oflow-toggle
	@echo '        hyprctl dispatch movetoworkspacesilent e+0,address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '        hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '    else' >> ~/.local/bin/oflow-toggle
	@echo '        FOCUSED=$$(hyprctl activewindow -j | jq -r ".address" 2>/dev/null)' >> ~/.local/bin/oflow-toggle
	@echo '        if [ "$$ADDR" = "$$FOCUSED" ]; then' >> ~/.local/bin/oflow-toggle
	@echo '            hyprctl dispatch movetoworkspacesilent special:hidden,address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '        else' >> ~/.local/bin/oflow-toggle
	@echo '            hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/oflow-toggle
	@echo '        fi' >> ~/.local/bin/oflow-toggle
	@echo '    fi' >> ~/.local/bin/oflow-toggle
	@echo 'else' >> ~/.local/bin/oflow-toggle
	@echo '    "$$OFLOW_BIN" &' >> ~/.local/bin/oflow-toggle
	@echo 'fi' >> ~/.local/bin/oflow-toggle
	@chmod +x ~/.local/bin/oflow-toggle
	@$(MAKE) setup-waybar
	@$(MAKE) setup-autostart
	@echo ""
	@echo "Installation complete!"
	@echo "  - Binary: ~/.local/bin/oflow"
	@echo "  - Click the ○ icon in Waybar to open settings"
	@echo "  - Press Super+D to start recording, press again to stop and transcribe"
	@echo ""
	@echo "Starting oflow..."
	@~/.local/bin/oflow-toggle

setup-waybar:
	@echo "Configuring Waybar..."
	@if [ -f ~/.config/waybar/config.jsonc ]; then \
		if ! grep -q '"custom/oflow"' ~/.config/waybar/config.jsonc; then \
			echo "Adding oflow module to Waybar config..."; \
			sed -i 's/"modules-center": \[\(.*\)\]/"modules-center": [\1, "custom/spacer", "custom/oflow"]/' ~/.config/waybar/config.jsonc; \
			echo "Adding spacer module..."; \
			sed -i '/^}/i \  "custom/spacer": {\n    "format": "  ",\n    "tooltip": false\n  },' ~/.config/waybar/config.jsonc; \
			echo "Adding oflow module definition..."; \
			sed -i '/^}/i \  "custom/oflow": {\n    "exec": "cat $$XDG_RUNTIME_DIR/oflow/state 2>/dev/null || echo '\''{\"text\":\"󰍬\",\"class\":\"idle\",\"tooltip\":\"oflow not running\"}\''',"\n    "return-type": "json",\n    "interval": 1,\n    "format": "{}",\n    "tooltip": true,\n    "on-click": "~/.local/bin/oflow-toggle"\n  },' ~/.config/waybar/config.jsonc; \
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
	@echo "Exec=$(HOME)/.local/bin/oflow --hidden" >> ~/.config/autostart/oflow.desktop
	@echo "Icon=audio-input-microphone" >> ~/.config/autostart/oflow.desktop
	@echo "Terminal=false" >> ~/.config/autostart/oflow.desktop
	@echo "Categories=Utility;AudioVideo;" >> ~/.config/autostart/oflow.desktop
	@echo "StartupNotify=false" >> ~/.config/autostart/oflow.desktop
	@echo "Autostart entry created (starts hidden)"

install-oflow-ctl:
	@echo "Installing oflow-ctl..."
	@mkdir -p ~/.local/bin
	@echo '#!/usr/bin/env python3' > ~/.local/bin/oflow-ctl
	@echo '"""Simple oflow socket controller for Hyprland bindings."""' >> ~/.local/bin/oflow-ctl
	@echo 'import socket' >> ~/.local/bin/oflow-ctl
	@echo 'import sys' >> ~/.local/bin/oflow-ctl
	@echo '' >> ~/.local/bin/oflow-ctl
	@echo 'def send_command(cmd):' >> ~/.local/bin/oflow-ctl
	@echo '    try:' >> ~/.local/bin/oflow-ctl
	@echo '        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)' >> ~/.local/bin/oflow-ctl
	@echo '        s.settimeout(1)' >> ~/.local/bin/oflow-ctl
	@echo "        s.connect('/tmp/voice-dictation.sock')" >> ~/.local/bin/oflow-ctl
	@echo '        s.send(cmd.encode())' >> ~/.local/bin/oflow-ctl
	@echo '        s.close()' >> ~/.local/bin/oflow-ctl
	@echo '    except Exception:' >> ~/.local/bin/oflow-ctl
	@echo '        pass  # Silently fail - socket might not exist' >> ~/.local/bin/oflow-ctl
	@echo '' >> ~/.local/bin/oflow-ctl
	@echo "if __name__ == '__main__':" >> ~/.local/bin/oflow-ctl
	@echo '    if len(sys.argv) > 1:' >> ~/.local/bin/oflow-ctl
	@echo '        send_command(sys.argv[1])' >> ~/.local/bin/oflow-ctl
	@chmod +x ~/.local/bin/oflow-ctl
	@echo "oflow-ctl installed"

setup-hotkey: install-oflow-ctl
	@echo "Setting up Super+D hotkey..."
	@if [ -f ~/.config/hypr/bindings.conf ]; then \
		if grep -q "# Oflow voice dictation" ~/.config/hypr/bindings.conf; then \
			sed -i '/# Oflow voice dictation/,/^$$/d' ~/.config/hypr/bindings.conf; \
		fi; \
		echo "" >> ~/.config/hypr/bindings.conf; \
		echo "# Oflow voice dictation (toggle: press Super+D to start/stop)" >> ~/.config/hypr/bindings.conf; \
		echo "bind = SUPER, D, exec, ~/.local/bin/oflow-ctl toggle" >> ~/.config/hypr/bindings.conf; \
		hyprctl reload 2>/dev/null || true; \
		echo "Hotkey configured: Super+D (toggle mode)"; \
	else \
		echo "Hyprland bindings.conf not found"; \
	fi

uninstall:
	@echo "Uninstalling oflow..."
	@rm -f ~/.local/bin/oflow ~/.local/bin/oflow-toggle ~/.local/bin/oflow-ctl
	@rm -f ~/.config/autostart/oflow.desktop
	@if [ -f ~/.config/hypr/bindings.conf ]; then \
		sed -i '/# Oflow voice dictation/,/^$$/d' ~/.config/hypr/bindings.conf; \
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
