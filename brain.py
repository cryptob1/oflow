"""oflow brain — persist captured notes (and later meetings) to a git-backed
Markdown vault.

The vault is a plain folder of Markdown files, Obsidian-compatible: notes append
to a per-day file; meetings (Phase 2) get their own file. After each write we
best-effort ``git add``/``commit`` so the vault is versioned and syncable across
machines. Both git and Obsidian are optional — a write is just filesystem I/O and
must never be lost because the vault isn't a repo or a commit fails.

Config (env, loadable via oflow's .env):
  OFLOW_BRAIN_DIR       vault root (default ~/brain)
  OFLOW_BRAIN_GIT       auto-commit each capture when the vault is a repo (default true)
  OFLOW_BRAIN_GIT_PUSH  also push after committing (default false)
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Config is read lazily (per call) so it reflects env loaded via oflow's .env,
# which is applied after this module is imported.


def _vault() -> Path:
    """Vault root — a folder of Markdown files. Point Obsidian at it; `git init` for sync."""
    return Path(os.environ.get("OFLOW_BRAIN_DIR", str(Path.home() / "brain"))).expanduser()


def _git_enabled() -> bool:
    """Auto-commit each capture when the vault is a git repo. Push is opt-in."""
    return os.environ.get("OFLOW_BRAIN_GIT", "true").lower() == "true"


def _git_push_enabled() -> bool:
    return os.environ.get("OFLOW_BRAIN_GIT_PUSH", "false").lower() == "true"


def _is_git_repo(vault: Path) -> bool:
    return (vault / ".git").is_dir()


def _git(vault: Path, *args: str) -> bool:
    """Run a git command in the vault. Best-effort: returns False (never raises)
    on any failure, so a capture is never lost to a git problem."""
    try:
        subprocess.run(
            ["git", "-C", str(vault), *args],
            check=True, capture_output=True, timeout=15,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        detail = getattr(e, "stderr", b"") or b""
        logger.debug(f"git {args[0] if args else ''} skipped/failed: {e} {detail!r}")
        return False


def _commit(vault: Path, path: Path, message: str) -> None:
    """Stage and commit one file to the vault repo (best-effort, opt-out via env)."""
    if not _git_enabled() or not _is_git_repo(vault):
        return
    if _git(vault, "add", str(path)) and _git(vault, "commit", "-m", message):
        if _git_push_enabled():
            _git(vault, "push")


def add_note(text: str, timestamp: datetime | None = None) -> Path:
    """Append a timestamped note to today's Markdown file and commit it.

    Returns the file written. Raises only on filesystem errors (the caller treats
    those as a failed capture); git problems are swallowed.
    """
    ts = timestamp or datetime.now()
    vault = _vault()
    notes_dir = vault / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    day_file = notes_dir / f"{ts:%Y-%m-%d}.md"

    new_file = not day_file.exists()
    with open(day_file, "a") as f:
        if new_file:
            f.write(f"# Notes — {ts:%Y-%m-%d}\n")
        f.write(f"\n## {ts:%H:%M}\n{text.strip()}\n")

    _commit(vault, day_file, f"note: {ts:%Y-%m-%d %H:%M}")
    logger.info(f"Note saved to {day_file}")
    return day_file
