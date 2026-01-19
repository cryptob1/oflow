#!/bin/bash
# Launcher script for bundled oflow backend
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VIRTUAL_ENV="$DIR/backend/venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
unset PYTHON_HOME
exec "$VIRTUAL_ENV/bin/python" "$DIR/backend/oflow.py" "$@"
