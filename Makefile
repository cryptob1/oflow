.PHONY: help run stop dev reload update _ensure-sidecar-stub build build-appimage build-sidecar test test-unit test-integration test-all format lint clean install install-appimage uninstall setup-backend setup-frontend setup-waybar setup-waybar-css setup-autostart setup-hotkey setup-osd install-cortex-ctl

SIDECAR_NAME := cortex-backend-x86_64-unknown-linux-gnu

help:
	@echo "Cortex - Voice Dictation for Hyprland/Wayland"
	@echo ""
	@echo "Available targets:"
	@echo "  make install         - Full install: build, install binary, setup Waybar & autostart"
	@echo "  make install-appimage- Build and install bundled AppImage (includes Python backend)"
	@echo "  make uninstall       - Remove cortex binary, Waybar config, and autostart"
	@echo "  make reload          - Restart app to pick up backend/overlay edits (no rebuild) ⚡"
	@echo "  make update          - Rebuild UI + reinstall + restart (after editing cortex-ui/)"
	@echo "  make dev             - Run in development mode (hot reload)"
	@echo "  make build           - Build release binary only (requires separate backend)"
	@echo "  make build-appimage  - Build bundled AppImage with embedded Python backend"
	@echo "  make build-sidecar   - Build PyInstaller sidecar binary only"
	@echo "  make run             - Start the backend server only"
	@echo "  make stop            - Stop all cortex processes"
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
	@if [ ! -d "cortex-ui/node_modules" ]; then \
		echo "Installing npm dependencies..."; \
		cd cortex-ui && npm install; \
	fi

dev: setup-backend setup-frontend
	@./scripts/dev.sh

build:
	@echo "Building Cortex for release..."
	@if [ ! -d "cortex-ui/node_modules" ]; then \
		echo "Installing npm dependencies..."; \
		cd cortex-ui && npm install; \
	fi
	@cd cortex-ui && npm run tauri build -- --no-bundle

build-sidecar: setup-backend
	@echo "Building Python sidecar with PyInstaller..."
	@mkdir -p cortex-ui/src-tauri/bin
	@uv pip install -q pyinstaller --python .venv/bin/python
	@.venv/bin/pyinstaller \
		--onefile \
		--clean \
		--strip \
		--name $(SIDECAR_NAME) \
		--distpath cortex-ui/src-tauri/bin \
		--specpath /tmp \
		--hidden-import sounddevice \
		--hidden-import _sounddevice_data \
		--hidden-import brain \
		--collect-all sounddevice \
		cortex.py
	@echo "Sidecar built: cortex-ui/src-tauri/bin/$(SIDECAR_NAME)"

build-appimage: build-sidecar setup-frontend
	@echo "Building AppImage with embedded Python backend..."
	@# NO_STRIP=1 needed for Arch - linuxdeploy's old strip can't handle new .relr.dyn sections
	@cd cortex-ui && NO_STRIP=1 npm run tauri build
	@echo ""
	@echo "AppImage built successfully!"
	@echo "Location: cortex-ui/src-tauri/target/release/bundle/appimage/"
	@ls -lh cortex-ui/src-tauri/target/release/bundle/appimage/*.AppImage 2>/dev/null || true

run:
	@echo "Starting Cortex backend..."
	@./cortex &

stop:
	@echo "Stopping Cortex..."
	@-pkill -f "python.*cortex.py" 2>/dev/null || true
	@-pkill -f "cortex-ui" 2>/dev/null || true
	@-pkill -f "cortex-backend" 2>/dev/null || true
	@-pkill -f "\.local/bin/cortex" 2>/dev/null || true
	@rm -f /tmp/cortex.pid /tmp/voice-dictation.sock 2>/dev/null || true
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

install: setup-backend _ensure-sidecar-stub build setup-hotkey setup-waybar setup-waybar-css setup-autostart setup-osd setup-dream
	@echo "Installing cortex..."
	@mkdir -p ~/.local/bin
	@cp cortex-ui/src-tauri/target/release/cortex-ui ~/.local/bin/cortex
	@chmod +x ~/.local/bin/cortex

setup-osd:
	@echo "Installing recording overlay (cortex-osd)..."
	@mkdir -p ~/.local/share/cortex
	@cp cortex-osd.py ~/.local/share/cortex/cortex-osd.py
	@echo "Overlay installed (needs: gtk4-layer-shell python-gobject python-cairo)"

setup-dream:
	@echo "Installing nightly dream + journal timer..."
	@mkdir -p ~/.config/systemd/user
	@printf '[Unit]\nDescription=cortex nightly dream (consolidate brain + write the daily journal)\n\n[Service]\nType=oneshot\nExecStart=%s/.local/bin/cortex-brain --dream\n' "$$HOME" > ~/.config/systemd/user/cortex-dream.service
	@printf '[Unit]\nDescription=Run the cortex dream nightly\n\n[Timer]\nOnCalendar=*-*-* 03:30:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' > ~/.config/systemd/user/cortex-dream.timer
	@systemctl --user daemon-reload 2>/dev/null || true
	@systemctl --user enable --now cortex-dream.timer 2>/dev/null || true
	@echo "  Dream/journal timer enabled (nightly 03:30)"

# --- Fast iteration ----------------------------------------------------------
# reload: restart the running app. Picks up Python backend (cortex.py) and
#         overlay (cortex-osd.py) edits with NO rebuild. Use this most of the time.
reload:
	@echo "Restarting cortex (hidden)..."
	@-kill $$(cat /tmp/cortex.pid 2>/dev/null) 2>/dev/null
	@-pkill -x cortex 2>/dev/null
	@sleep 1
	@rm -f /tmp/voice-dictation.sock /tmp/cortex.pid
	@cp cortex-osd.py ~/.local/share/cortex/cortex-osd.py 2>/dev/null || true
	@setsid ~/.local/bin/cortex --hidden >/dev/null 2>&1 & true
	@sleep 2
	@if [ -S /tmp/voice-dictation.sock ]; then echo "✅ cortex running (hidden), backend up"; else echo "⚠️  backend not up — check 'make run'"; fi

# update: rebuild the UI (Rust/React), reinstall, and restart. Use after editing
#         anything under cortex-ui/. Creates the dev sidecar stub if missing.
update: _ensure-sidecar-stub
	@echo "Building cortex UI (release)..."
	@cd cortex-ui && npm run tauri build -- --no-bundle
	@echo "Stopping running cortex (so the binary isn't busy)..."
	@-kill $$(cat /tmp/cortex.pid 2>/dev/null) 2>/dev/null
	@-pkill -x cortex 2>/dev/null
	@sleep 1
	@rm -f /tmp/voice-dictation.sock /tmp/cortex.pid
	@echo "Installing..."
	@cp cortex-ui/src-tauri/target/release/cortex-ui ~/.local/bin/cortex
	@chmod +x ~/.local/bin/cortex
	@mkdir -p ~/.local/share/cortex && cp cortex-osd.py ~/.local/share/cortex/cortex-osd.py
	@setsid ~/.local/bin/cortex --hidden >/dev/null 2>&1 & true
	@sleep 2
	@if [ -S /tmp/voice-dictation.sock ]; then echo "✅ cortex updated & running (hidden)"; else echo "⚠️  backend not up — check 'make run'"; fi

_ensure-sidecar-stub:
	@mkdir -p cortex-ui/src-tauri/bin
	@if [ ! -e cortex-ui/src-tauri/bin/$(SIDECAR_NAME) ]; then \
		printf '#!/bin/bash\nexec "$$HOME/code/cortex/.venv/bin/python" "$$HOME/code/cortex/cortex.py" "$$@"\n' > cortex-ui/src-tauri/bin/$(SIDECAR_NAME); \
		chmod +x cortex-ui/src-tauri/bin/$(SIDECAR_NAME); \
	fi
	@echo '#!/bin/bash' > ~/.local/bin/cortex-toggle
	@echo 'CORTEX_BIN="$$HOME/.local/bin/cortex"' >> ~/.local/bin/cortex-toggle
	@echo 'WIN_CLASS="cortex"' >> ~/.local/bin/cortex-toggle
	@echo 'ADDR=$$(hyprctl clients -j | jq -r ".[] | select(.class == \"$$WIN_CLASS\") | .address" | head -1)' >> ~/.local/bin/cortex-toggle
	@echo 'if [ -n "$$ADDR" ] && [ "$$ADDR" != "null" ]; then' >> ~/.local/bin/cortex-toggle
	@echo '    WS=$$(hyprctl clients -j | jq -r ".[] | select(.address == \"$$ADDR\") | .workspace.name")' >> ~/.local/bin/cortex-toggle
	@echo '    if [[ "$$WS" == special:* ]]; then' >> ~/.local/bin/cortex-toggle
	@echo '        hyprctl dispatch movetoworkspacesilent e+0,address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '        hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '    else' >> ~/.local/bin/cortex-toggle
	@echo '        FOCUSED=$$(hyprctl activewindow -j | jq -r ".address" 2>/dev/null)' >> ~/.local/bin/cortex-toggle
	@echo '        if [ "$$ADDR" = "$$FOCUSED" ]; then' >> ~/.local/bin/cortex-toggle
	@echo '            hyprctl dispatch movetoworkspacesilent special:hidden,address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '        else' >> ~/.local/bin/cortex-toggle
	@echo '            hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '        fi' >> ~/.local/bin/cortex-toggle
	@echo '    fi' >> ~/.local/bin/cortex-toggle
	@echo 'else' >> ~/.local/bin/cortex-toggle
	@echo '    "$$CORTEX_BIN" &' >> ~/.local/bin/cortex-toggle
	@echo 'fi' >> ~/.local/bin/cortex-toggle
	@chmod +x ~/.local/bin/cortex-toggle
	@echo "Installing brain search launcher (cortex-brain)..."
	@printf '#!/bin/bash\nexec "$$HOME/code/cortex/.venv/bin/python" "$$HOME/code/cortex/brain_search.py" "$$@"\n' > ~/.local/bin/cortex-brain
	@chmod +x ~/.local/bin/cortex-brain
	@echo "Creating app-menu launcher (cortex.desktop)..."
	@mkdir -p ~/.local/share/applications
	@printf '[Desktop Entry]\nType=Application\nName=cortex\nGenericName=Voice Typing & Second Brain\nComment=Voice dictation, notes (Copilot+N) and meetings (Copilot+M)\nExec=%s/.local/bin/cortex-toggle\nIcon=audio-input-microphone\nTerminal=false\nCategories=Utility;AudioVideo;Audio;\nKeywords=voice;dictation;transcription;notes;meetings;brain;\nStartupWMClass=cortex\n' "$$HOME" > ~/.local/share/applications/cortex.desktop
	@update-desktop-database ~/.local/share/applications 2>/dev/null || true
	@echo ""
	@echo "Installation complete!"
	@echo "  - Binary: ~/.local/bin/cortex"
	@echo "  - UI auto-starts backend when launched"
	@echo "  - Click the mic icon in Waybar to open settings"
	@echo "  - Press Super+D to start recording, press again to stop"
	@echo ""
	@echo "Starting cortex..."
	@~/.local/bin/cortex-toggle

install-appimage: build-appimage setup-hotkey
	@echo "Installing cortex AppImage..."
	@mkdir -p ~/.local/bin
	@APPIMAGE=$$(ls cortex-ui/src-tauri/target/release/bundle/appimage/*.AppImage 2>/dev/null | head -1); \
	if [ -n "$$APPIMAGE" ]; then \
		cp "$$APPIMAGE" ~/.local/bin/cortex; \
		chmod +x ~/.local/bin/cortex; \
	else \
		echo "ERROR: AppImage not found!"; \
		exit 1; \
	fi
	@echo '#!/bin/bash' > ~/.local/bin/cortex-toggle
	@echo 'CORTEX_BIN="$$HOME/.local/bin/cortex"' >> ~/.local/bin/cortex-toggle
	@echo 'WIN_CLASS="cortex"' >> ~/.local/bin/cortex-toggle
	@echo 'ADDR=$$(hyprctl clients -j | jq -r ".[] | select(.class == \"$$WIN_CLASS\") | .address" | head -1)' >> ~/.local/bin/cortex-toggle
	@echo 'if [ -n "$$ADDR" ] && [ "$$ADDR" != "null" ]; then' >> ~/.local/bin/cortex-toggle
	@echo '    WS=$$(hyprctl clients -j | jq -r ".[] | select(.address == \"$$ADDR\") | .workspace.name")' >> ~/.local/bin/cortex-toggle
	@echo '    if [[ "$$WS" == special:* ]]; then' >> ~/.local/bin/cortex-toggle
	@echo '        hyprctl dispatch movetoworkspacesilent e+0,address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '        hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '    else' >> ~/.local/bin/cortex-toggle
	@echo '        FOCUSED=$$(hyprctl activewindow -j | jq -r ".address" 2>/dev/null)' >> ~/.local/bin/cortex-toggle
	@echo '        if [ "$$ADDR" = "$$FOCUSED" ]; then' >> ~/.local/bin/cortex-toggle
	@echo '            hyprctl dispatch movetoworkspacesilent special:hidden,address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '        else' >> ~/.local/bin/cortex-toggle
	@echo '            hyprctl dispatch focuswindow address:$$ADDR' >> ~/.local/bin/cortex-toggle
	@echo '        fi' >> ~/.local/bin/cortex-toggle
	@echo '    fi' >> ~/.local/bin/cortex-toggle
	@echo 'else' >> ~/.local/bin/cortex-toggle
	@echo '    "$$CORTEX_BIN" &' >> ~/.local/bin/cortex-toggle
	@echo 'fi' >> ~/.local/bin/cortex-toggle
	@chmod +x ~/.local/bin/cortex-toggle
	@$(MAKE) setup-waybar
	@$(MAKE) setup-waybar-css
	@$(MAKE) setup-autostart
	@echo ""
	@echo "Installation complete!"
	@echo "  - AppImage: ~/.local/bin/cortex"
	@echo "  - Backend is embedded - no separate process needed"
	@echo "  - Click the mic icon in Waybar to open settings"
	@echo "  - Press Super+D to start recording, press again to stop"
	@echo ""
	@echo "Starting cortex..."
	@~/.local/bin/cortex-toggle

setup-waybar:
	@echo "Configuring Waybar..."
	@if [ -f ~/.config/waybar/config.jsonc ]; then \
		if ! jq -e '."custom/cortex"' ~/.config/waybar/config.jsonc >/dev/null 2>&1; then \
			echo "Adding cortex module to Waybar config..."; \
			jq '."modules-center" = (["clock", "custom/cortex"] + (."modules-center" | map(select(. != "clock")))) | ."custom/cortex" = {"exec": "cat $$XDG_RUNTIME_DIR/cortex/state 2>/dev/null || echo '"'"'{\"text\":\"󰍬\",\"class\":\"idle\",\"tooltip\":\"cortex not running\"}'"'"'", "return-type": "json", "interval": 1, "format": "{}", "tooltip": true, "on-click": "~/.local/bin/cortex-toggle"}' ~/.config/waybar/config.jsonc > /tmp/waybar-cortex-config.jsonc && \
			mv /tmp/waybar-cortex-config.jsonc ~/.config/waybar/config.jsonc; \
			pkill -SIGUSR2 waybar 2>/dev/null || true; \
			echo "Waybar module added"; \
		else \
			echo "Waybar cortex module already configured"; \
		fi \
	else \
		echo "Waybar config not found at ~/.config/waybar/config.jsonc"; \
	fi

setup-waybar-css:
	@echo "Setting up Waybar CSS styling..."
	@if [ -f ~/.config/waybar/style.css ]; then \
		if ! grep -q "#custom-cortex" ~/.config/waybar/style.css; then \
			echo "" >> ~/.config/waybar/style.css; \
			cat waybar-cortex-style.css >> ~/.config/waybar/style.css; \
			echo "Waybar CSS styling added"; \
		else \
			echo "Waybar CSS already configured"; \
		fi \
	else \
		echo "Creating Waybar style.css with cortex styles..."; \
		mkdir -p ~/.config/waybar; \
		cat waybar-cortex-style.css > ~/.config/waybar/style.css; \
	fi
	@pkill -SIGUSR2 waybar 2>/dev/null || echo "Waybar will apply styles on next restart"

setup-autostart:
	@echo "Setting up autostart..."
	@mkdir -p ~/.config/autostart
	@echo "[Desktop Entry]" > ~/.config/autostart/cortex.desktop
	@echo "Type=Application" >> ~/.config/autostart/cortex.desktop
	@echo "Name=cortex" >> ~/.config/autostart/cortex.desktop
	@echo "Comment=Voice dictation for Hyprland" >> ~/.config/autostart/cortex.desktop
	@echo "Exec=$(HOME)/.local/bin/cortex --hidden" >> ~/.config/autostart/cortex.desktop
	@echo "Icon=audio-input-microphone" >> ~/.config/autostart/cortex.desktop
	@echo "Terminal=false" >> ~/.config/autostart/cortex.desktop
	@echo "Categories=Utility;AudioVideo;" >> ~/.config/autostart/cortex.desktop
	@echo "StartupNotify=false" >> ~/.config/autostart/cortex.desktop
	@echo "Autostart entry created (starts hidden)"

install-cortex-ctl:
	@echo "Installing cortex-ctl + cortex-hotkey..."
	@mkdir -p ~/.local/bin
	@install -m755 scripts/cortex-ctl ~/.local/bin/cortex-ctl
	@install -m755 scripts/cortex-hotkey ~/.local/bin/cortex-hotkey
	@echo "cortex-ctl + cortex-hotkey installed"

# Push-to-talk hotkey for dictation. Default: the Microsoft Copilot key (emits
# Super+Shift+F23). Switchable any time from the app's Settings, or here at
# install with CORTEX_HOTKEY=f8 for keyboards without a Copilot key.
CORTEX_HOTKEY ?= copilot

setup-hotkey: install-cortex-ctl
	@echo "Setting up cortex hotkey ($(CORTEX_HOTKEY) push-to-talk)..."
	@~/.local/bin/cortex-hotkey $(CORTEX_HOTKEY) || echo "Hyprland not detected — skipped hotkey setup"

uninstall:
	@echo "Uninstalling cortex..."
	@echo "  Removing binaries..."
	@rm -f ~/.local/bin/cortex ~/.local/bin/cortex-toggle ~/.local/bin/cortex-ctl
	@echo "  Removing autostart entry..."
	@rm -f ~/.config/autostart/cortex.desktop
	@echo "  Removing Hyprland hotkey..."
	@if [ -f ~/.config/hypr/bindings.conf ]; then \
		sed -i '/# Cortex voice dictation/,/^$$/d' ~/.config/hypr/bindings.conf; \
		hyprctl reload 2>/dev/null || true; \
	fi
	@echo "  Removing Waybar module..."
	@if [ -f ~/.config/waybar/config.jsonc ] && jq -e '."custom/cortex"' ~/.config/waybar/config.jsonc >/dev/null 2>&1; then \
		jq 'del(."custom/cortex") | ."modules-left" = (."modules-left" | map(select(. != "custom/cortex"))) | ."modules-center" = (."modules-center" // [] | map(select(. != "custom/cortex"))) | ."modules-right" = (."modules-right" // [] | map(select(. != "custom/cortex")))' ~/.config/waybar/config.jsonc > /tmp/waybar-cortex-uninstall.jsonc && \
		mv /tmp/waybar-cortex-uninstall.jsonc ~/.config/waybar/config.jsonc; \
	fi
	@echo "  Removing Waybar CSS..."
	@if [ -f ~/.config/waybar/style.css ]; then \
		sed -i '/\/\* Cortex voice dictation/,/^}/d' ~/.config/waybar/style.css 2>/dev/null || true; \
		sed -i '/#custom-cortex\b/,/^}/d' ~/.config/waybar/style.css 2>/dev/null || true; \
		sed -i '/\/\* cortex \*\//d' ~/.config/waybar/style.css 2>/dev/null || true; \
	fi
	@pkill -SIGUSR2 waybar 2>/dev/null || true
	@echo "  Removing runtime files..."
	@rm -rf $$XDG_RUNTIME_DIR/cortex 2>/dev/null || true
	@rm -f /tmp/cortex.pid /tmp/voice-dictation.sock 2>/dev/null || true
	@echo "  Removing settings..."
	@rm -rf ~/.cortex 2>/dev/null || true
	@$(MAKE) stop
	@echo ""
	@echo "Uninstall complete! cortex has been removed from your system."

clean:
	@echo "Cleaning cache files..."
	@rm -rf __pycache__ .pytest_cache .ruff_cache .coverage htmlcov
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Clean complete"
