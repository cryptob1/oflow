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

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"

# Config is read lazily (per call) so it reflects both env (advanced overrides,
# loaded via oflow's .env after this module is imported) and settings.json (what
# the UI writes). Precedence: env var > settings.json > built-in default.


def _settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def _vault() -> Path:
    """Vault root — a folder of Markdown files. Point Obsidian at it; `git init` for sync."""
    env = os.environ.get("OFLOW_BRAIN_DIR")
    path = env or _settings().get("brainVaultPath") or str(Path.home() / "brain")
    return Path(path).expanduser()


def _git_enabled() -> bool:
    """Auto-commit each capture when the vault is a git repo. Push is opt-in."""
    env = os.environ.get("OFLOW_BRAIN_GIT")
    if env is not None:
        return env.lower() == "true"
    return bool(_settings().get("brainGit", True))


def _git_push_enabled() -> bool:
    env = os.environ.get("OFLOW_BRAIN_GIT_PUSH")
    if env is not None:
        return env.lower() == "true"
    return bool(_settings().get("brainGitPush", False))


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
    """Write a note to its own Markdown file and commit it.

    One file per note (not per day) so captures from different machines never
    collide when the vault is synced (Syncthing/Obsidian) across laptops. Returns
    the file written; raises only on filesystem errors (git problems are swallowed).
    """
    ts = timestamp or datetime.now()
    vault = _vault()
    notes_dir = vault / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Unique filename to the second, with a suffix guard for same-second captures.
    base = f"{ts:%Y-%m-%d-%H%M%S}"
    note_file = notes_dir / f"{base}.md"
    n = 2
    while note_file.exists():
        note_file = notes_dir / f"{base}-{n}.md"
        n += 1

    note_file.write_text(
        f"---\ncreated: {ts.isoformat()}\ntype: note\nsource: oflow\n---\n\n{text.strip()}\n"
    )
    _commit(vault, note_file, f"note: {ts:%Y-%m-%d %H:%M}")
    logger.info(f"Note saved to {note_file}")
    return note_file


def add_meeting(transcript: str, summary: str, timestamp: datetime | None = None) -> Path:
    """Write a meeting (summary + full transcript) to its own Markdown file and
    commit it. One file per meeting under ``meetings/``. Returns the file path."""
    ts = timestamp or datetime.now()
    vault = _vault()
    meetings_dir = vault / "meetings"
    meetings_dir.mkdir(parents=True, exist_ok=True)
    mfile = meetings_dir / f"{ts:%Y-%m-%d-%H%M}.md"

    with open(mfile, "w") as f:
        f.write(f"---\ncreated: {ts.isoformat()}\ntype: meeting\nsource: oflow\n---\n\n")
        f.write(f"# Meeting — {ts:%Y-%m-%d %H:%M}\n\n")
        if summary.strip():
            f.write(f"{summary.strip()}\n\n")
        f.write(f"## Transcript\n\n{transcript.strip()}\n")

    _commit(vault, mfile, f"meeting: {ts:%Y-%m-%d %H:%M}")
    logger.info(f"Meeting saved to {mfile}")
    return mfile
