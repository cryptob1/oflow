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
import re
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


# --------------------------------------------------------------------------- #
# Universal item model
#
# Every item in the brain — note, meeting, initiative, and future types like
# reminder/task — is a Markdown file with YAML frontmatter, discriminated by a
# `type` field and stored in a folder per type. Shared frontmatter: id, type,
# created, source, title, tags, links. Type-specific fields go in `extra`.
# Adding a new type = a thin wrapper around write_item(), nothing else.
# --------------------------------------------------------------------------- #
def new_id(ts: datetime | None = None) -> str:
    """Stable, sortable id (millisecond precision). Links target this, not the
    filename, so items can be renamed/reorganized without breaking relations."""
    ts = ts or datetime.now()
    return ts.strftime("%Y%m%dT%H%M%S%f")[:-3]


def _yaml_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines.extend(f"  - {item}" for item in v)
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


def write_item(
    item_type: str,
    folder: str,
    basename: str,
    *,
    title: str = "",
    body: str = "",
    tags: list[str] | None = None,
    links: list[str] | None = None,
    extra: dict | None = None,
    source: str = "oflow",
    timestamp: datetime | None = None,
    item_id: str | None = None,
) -> Path:
    """Write one brain item as frontmatter Markdown and commit it. Returns the path.

    `basename` gets a `-N` suffix if it already exists, so captures never collide
    (important when the vault is synced across machines).
    """
    ts = timestamp or datetime.now()
    vault = _vault()
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{basename}.md"
    n = 2
    while path.exists():
        path = d / f"{basename}-{n}.md"
        n += 1

    fields = {
        "id": item_id or new_id(ts),
        "type": item_type,
        "created": ts.isoformat(),
        "source": source,
    }
    if title:
        fields["title"] = title
    if tags:
        fields["tags"] = tags
    if links:
        fields["links"] = links
    if extra:
        fields.update(extra)

    path.write_text(_yaml_frontmatter(fields) + "\n" + body)
    _commit(vault, path, f"{item_type}: {title or basename}")
    logger.info(f"{item_type.capitalize()} saved to {path}")
    return path


def _title_from_text(text: str, max_words: int = 8, max_chars: int = 60) -> str:
    """A short human title from a capture's first sentence — so notes read as
    titles in Obsidian instead of timestamps. The timestamp lives in `created`."""
    first = re.split(r"[.!?\n]", text.strip(), maxsplit=1)[0].strip(" ,.-—")
    title = " ".join(first.split()[:max_words])
    if len(title) > max_chars:
        title = title[:max_chars].rsplit(" ", 1)[0]
    return title or "note"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        try:
            return text[text.index("\n---\n", 4) + 5:]
        except ValueError:
            pass
    return text


def add_note(text: str, timestamp: datetime | None = None) -> Path:
    """Save a dictated note under a human title derived from its content (one file
    per note, so cross-machine syncs never collide)."""
    ts = timestamp or datetime.now()
    title = _title_from_text(text)
    return write_item(
        "note", "notes", _slugify(title), title=title,
        body=text.strip() + "\n", source="oflow-note", timestamp=ts,
    )


def retitle_items(kinds: tuple[str, ...] = ("notes", "reminders")) -> int:
    """One-off migration: give existing timestamp-named items a human title +
    filename derived from their content, preserving all frontmatter (incl. links)."""
    vault, n = _vault(), 0
    for kind in kinds:
        d = vault / kind
        if not d.exists():
            continue
        for f in list(d.glob("*.md")):
            text = f.read_text()
            if re.search(r"^title:", text, re.MULTILINE):
                continue  # already has a title
            title = _title_from_text(_strip_frontmatter(text).strip())
            new_text = re.sub(r"\n---\n", f"\ntitle: {title}\n---\n", text, count=1)
            newpath = d / f"{_slugify(title)}.md"
            i = 2
            while newpath.exists() and newpath != f:
                newpath = d / f"{_slugify(title)}-{i}.md"
                i += 1
            newpath.write_text(new_text)
            if newpath != f:
                f.unlink()
            n += 1
    return n


def _slugify(name: str) -> str:
    s = name.lower().replace("'", "").replace("’", "")  # kyra's -> kyras
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"


def add_initiative(name: str, goals: list[str], note: str = "",
                   timestamp: datetime | None = None) -> Path:
    """Create an initiative (a goal/theme that captures link to), with goals in
    frontmatter and a running Log section."""
    ts = timestamp or datetime.now()
    body = f"# {name}\n\n"
    if goals:
        body += "## Goals\n" + "".join(f"- {g}\n" for g in goals) + "\n"
    body += "## Log\n"
    if note.strip():
        body += f"\n### {ts:%Y-%m-%d %H:%M} — created\n{note.strip()}\n"
    return write_item(
        "initiative", "initiatives", _slugify(name),
        title=name, body=body, source="oflow-note", timestamp=ts,
        extra={"status": "active", "goals": goals},
    )


def add_reminder(task: str, due: str = "", note: str = "",
                 timestamp: datetime | None = None) -> Path:
    """Create a reminder (task + optional ISO `due` datetime). `status` starts
    'pending'; the backend fires a notification when a due one comes up."""
    ts = timestamp or datetime.now()
    body = f"# {task}\n"
    if due:
        body += f"\n**Due:** {due}\n"
    if note.strip():
        body += f"\n{note.strip()}\n"
    return write_item(
        "reminder", "reminders", _slugify(task), title=task, body=body,
        source="oflow-note", timestamp=ts, extra={"due": due, "status": "pending"},
    )


def _read_frontmatter(path: Path) -> dict:
    """Parse scalar frontmatter fields (status/due/title/type/…). Lists are skipped."""
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}
    fm = {}
    for line in text[4:end].split("\n"):
        m = re.match(r"^(\w+):\s*(.+)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def due_reminders(now: datetime | None = None) -> list[tuple[Path, str, datetime]]:
    """Pending reminders whose `due` is at or before `now` — (path, task, due)."""
    now = now or datetime.now()
    d = _vault() / "reminders"
    out = []
    if not d.exists():
        return out
    for f in d.glob("*.md"):
        fm = _read_frontmatter(f)
        if fm.get("status") != "pending" or not fm.get("due"):
            continue
        try:
            due_dt = datetime.fromisoformat(fm["due"])
        except ValueError:
            continue
        if due_dt <= now:
            out.append((f, fm.get("title", "reminder"), due_dt))
    return out


def mark_reminder(path: Path, status: str = "done") -> None:
    """Flip a reminder's status (pending → done) and commit."""
    text = path.read_text()
    new = re.sub(r"^status:\s*\w+$", f"status: {status}", text, count=1, flags=re.MULTILINE)
    if new != text:
        path.write_text(new)
        _commit(_vault(), path, f"reminder {status}: {path.name}")


def add_meeting(transcript: str, summary: str, timestamp: datetime | None = None) -> Path:
    """Write a meeting (summary + full transcript) to its own Markdown file."""
    ts = timestamp or datetime.now()
    title = f"Meeting — {ts:%Y-%m-%d %H:%M}"
    body = f"# {title}\n\n"
    if summary.strip():
        body += f"{summary.strip()}\n\n"
    body += f"## Transcript\n\n{transcript.strip()}\n"
    return write_item(
        "meeting", "meetings", f"{ts:%Y-%m-%d-%H%M}",
        title=title, body=body, source="oflow-meeting", timestamp=ts,
    )
