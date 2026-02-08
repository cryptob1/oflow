.PHONY: install run uninstall dev clean

# Install oflow-macos and dependencies
install:
	uv venv
	uv pip install -e .
	@echo ""
	@echo "=== oflow-macos installed ==="
	@echo ""
	@echo "NOTE: For now, oflow-macos must be started from an interactive Terminal session."
	@echo "Leave that Terminal window open while using oflow."
	@echo ""
	@echo "IMPORTANT: Grant these macOS permissions to your Terminal app before first use:"
	@echo "  1. Accessibility:   System Settings > Privacy & Security > Accessibility"
	@echo "  2. Input Monitoring: System Settings > Privacy & Security > Input Monitoring"
	@echo "  3. Microphone:      System Settings > Privacy & Security > Microphone"
	@echo ""
	@echo "Start: make run"
	@echo "or:    source .venv/bin/activate && oflow-macos"

# Run oflow-macos
run:
	@test -x .venv/bin/python || (echo "Error: venv not found. Run 'make install' first." && exit 1)
	.venv/bin/python oflow_macos.py

# Install dependencies only (without editable install)
dev:
	uv venv
	uv pip install -r requirements.txt

# Uninstall
uninstall:
	uv pip uninstall oflow-macos
	@echo "oflow-macos uninstalled"

# Clean up runtime files
clean:
	rm -f /tmp/oflow-macos.pid /tmp/oflow-macos.sock
	@echo "Cleaned up runtime files"
