"""oflow brain search — local semantic Q&A over the note/meeting vault.

A lean, private RAG: chunk the vault's Markdown, embed it locally with fastembed
(ONNX, no API key), retrieve the chunks most similar to a question, and let Groq
synthesize a cited answer. The index lives under the vault in ``.index/``
(git/sync-ignored, rebuildable) — the Markdown stays the source of truth.

CLI:
    python brain_search.py "what did we decide about onboarding?"
    python brain_search.py --reindex        # rebuild the index, then exit
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

import brain  # reuse vault + settings resolution

logger = logging.getLogger(__name__)

EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, ~130MB, English
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
ANSWER_MODEL = "llama-3.3-70b-versatile"


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
def _index_dir():
    d = brain._vault() / ".index"
    d.mkdir(parents=True, exist_ok=True)
    return d


_EMBEDDER = None


def _embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding
        _EMBEDDER = TextEmbedding(model_name=EMBED_MODEL)
    return _EMBEDDER


def _embed(texts: list[str]) -> np.ndarray:
    vecs = np.array(list(_embedder().embed(texts)), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9  # cosine via dot
    return vecs


def _chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Split on blank lines, then pack paragraphs into ~`size`-char chunks."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            # a single huge paragraph (long transcript) gets sliced with overlap
            if len(p) > size:
                for i in range(0, len(p), size - overlap):
                    chunks.append(p[i:i + size])
                cur = ""
            else:
                cur = p
    if cur:
        chunks.append(cur)
    return chunks


def build_index() -> int:
    """(Re)build the vault index. Returns the number of chunks indexed."""
    vault = brain._vault()
    docs: list[dict] = []
    for kind in ("notes", "meetings", "initiatives"):
        d = vault / kind
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            for ch in _chunk(f.read_text()):
                docs.append({"source": f"{kind}/{f.name}", "type": kind[:-1], "text": ch})
    if not docs:
        return 0
    vectors = _embed([d["text"] for d in docs])
    np.save(_index_dir() / "vectors.npy", vectors)
    (_index_dir() / "docs.json").write_text(json.dumps(docs))
    logger.info(f"Indexed {len(docs)} chunks from {vault}")
    return len(docs)


def _load_index():
    idx = _index_dir()
    if not (idx / "docs.json").exists() or not (idx / "vectors.npy").exists():
        return None, None
    docs = json.loads((idx / "docs.json").read_text())
    vectors = np.load(idx / "vectors.npy")
    return docs, vectors


def _index_stale() -> bool:
    """True if any vault Markdown is newer than the index (or it doesn't exist)."""
    docs_file = _index_dir() / "docs.json"
    if not docs_file.exists():
        return True
    idx_mtime = docs_file.stat().st_mtime
    vault = brain._vault()
    for kind in ("notes", "meetings"):
        d = vault / kind
        if d.exists() and any(f.stat().st_mtime > idx_mtime for f in d.glob("*.md")):
            return True
    return False


def search(query: str, k: int = 6) -> list[tuple[dict, float]]:
    """Return the top-k (doc, score) chunks most similar to the query.

    Rebuilds the index first if the vault has changed since it was last built, so
    freshly captured notes/meetings are searchable without a manual --reindex.
    """
    if _index_stale():
        build_index()
    docs, vectors = _load_index()
    if docs is None:
        return []
    q = _embed([query])[0]
    sims = vectors @ q
    top = np.argsort(-sims)[:k]
    return [(docs[i], float(sims[i])) for i in top]


# --------------------------------------------------------------------------- #
# Answer synthesis (Groq)
# --------------------------------------------------------------------------- #
def _groq_key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or brain._settings().get("groqApiKey")


def answer(query: str, k: int = 6) -> tuple[str, list[str]]:
    """Retrieve relevant chunks and synthesize a cited answer via Groq.

    Returns (answer_text, sources). Falls back to a plain retrieval listing if no
    Groq key is configured.
    """
    hits = search(query, k)
    if not hits:
        return ("Your brain is empty — capture a note (Copilot+N) or meeting "
                "(Copilot+M) first.", [])
    sources = list(dict.fromkeys(h[0]["source"] for h in hits))  # unique, ordered
    context = "\n\n".join(f"[{h[0]['source']}]\n{h[0]['text']}" for h in hits)

    key = _groq_key()
    if not key:
        listing = "\n\n".join(f"— {h[0]['source']} (score {h[1]:.2f})\n{h[0]['text'][:300]}" for h in hits)
        return (f"(No Groq key set — showing raw matches.)\n\n{listing}", sources)

    import httpx
    system = (
        "You answer the user's question using ONLY the provided excerpts from their "
        "personal notes and meeting transcripts. Cite the source filename in brackets "
        "after each claim, e.g. [meetings/2026-07-17-1049.md]. If the excerpts don't "
        "contain the answer, say so plainly. Be concise."
    )
    user = f"Question: {query}\n\nExcerpts:\n{context}"
    try:
        resp = httpx.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": ANSWER_MODEL,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0.2,
                "max_tokens": 700,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip(), sources
        return f"Groq error {resp.status_code}: {resp.text[:200]}", sources
    except Exception as e:
        return f"Search LLM error: {e}", sources


# --------------------------------------------------------------------------- #
# Auto-mapping: link notes/meetings to related initiatives (semantic)
# --------------------------------------------------------------------------- #
# bge-small has a high similarity baseline (unrelated pairs ~0.45-0.57, related
# ~0.6-0.75), so 0.6 is the clean cut between "about this goal" and coincidental.
LINK_THRESHOLD = float(os.environ.get("OFLOW_LINK_THRESHOLD", "0.6"))
LINK_MAX = int(os.environ.get("OFLOW_LINK_MAX", "2"))

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _split(text: str) -> tuple[str, str]:
    """Return (frontmatter, body); frontmatter is '' if the file has none."""
    m = _FM_RE.match(text)
    return (m.group(1), text[m.end():]) if m else ("", text)


def _fm_field(fm: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", fm, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _match_text(path: Path) -> str:
    """Text used to match an item to initiatives — a meeting uses only its summary
    (not the long transcript, which would dilute the match)."""
    fm, body = _split(path.read_text())
    if _fm_field(fm, "type") == "meeting":
        body = body.split("## Transcript", 1)[0]
    return body.strip()[:2000]


def _initiatives() -> list[dict]:
    d = brain._vault() / "initiatives"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.md")):
        fm, body = _split(f.read_text())
        title = _fm_field(fm, "title") or f.stem
        # Profile = title + goals only; the Log grows over time and would drift the match.
        goals = "\n".join(re.findall(r"^\s*-\s*(.+)$", body.split("## Log", 1)[0], re.MULTILINE))
        out.append({"file": f, "id": _fm_field(fm, "id") or f.stem,
                    "title": title, "text": f"{title}\n{goals}"})
    return out


def _add_links_frontmatter(path: Path, new_links: list[str]) -> list[str]:
    """Merge new_links into the item's `links:` frontmatter list; return those added."""
    fm, body = _split(path.read_text())
    if not fm:
        return []
    lines, existing, kept, i = fm.split("\n"), [], [], 0
    while i < len(lines):
        if lines[i].startswith("links:"):
            i += 1
            while i < len(lines) and lines[i].startswith("  - "):
                existing.append(lines[i][4:].strip()); i += 1
            continue
        kept.append(lines[i]); i += 1
    added = [l for l in new_links if l not in existing]
    block = [x for x in kept if x.strip()]
    merged = existing + added
    if merged:
        block += ["links:"] + [f"  - {l}" for l in merged]
    path.write_text("---\n" + "\n".join(block) + "\n---\n" + body)
    return added


def link_item(item_path) -> list[str]:
    """Link a note/meeting to related initiative(s) by semantic similarity.
    Adds the initiative ids to the item's `links` and back-logs into each
    initiative. Returns the titles it linked to."""
    item_path = Path(item_path)
    inits = _initiatives()
    if not item_path.exists() or not inits:
        return []
    text = _match_text(item_path)
    if not text:
        return []

    vecs = _embed([text] + [i["text"] for i in inits])
    sims = vecs[1:] @ vecs[0]
    matched = [i for s, i in sorted(zip(sims, inits), key=lambda x: -x[0]) if s >= LINK_THRESHOLD][:LINK_MAX]
    if not matched:
        return []

    _add_links_frontmatter(item_path, [i["id"] for i in matched])
    _, body = _split(item_path.read_text())
    snippet = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")), "")[:100]
    for i in matched:
        with open(i["file"], "a") as f:
            f.write(f"- [[{item_path.stem}]] ({item_path.parent.name}) — {snippet}\n")

    vault = brain._vault()
    if brain._git_enabled() and brain._is_git_repo(vault):
        for p in [item_path, *[i["file"] for i in matched]]:
            brain._git(vault, "add", str(p))
        if brain._git(vault, "commit", "-m", f"link {item_path.name} → {len(matched)} initiative(s)"):
            if brain._git_push_enabled():
                brain._git(vault, "push")
    logger.info(f"Linked {item_path.name} → {[i['title'] for i in matched]}")
    return [i["title"] for i in matched]


def link_all() -> int:
    """Backfill: link every existing note & meeting to initiatives."""
    vault, n = brain._vault(), 0
    for kind in ("notes", "meetings"):
        d = vault / kind
        if d.exists():
            for f in sorted(d.glob("*.md")):
                if link_item(f):
                    n += 1
    return n


# --------------------------------------------------------------------------- #
# Initiative synthesis (the "coach")
# --------------------------------------------------------------------------- #
def _fm_list(fm: str, key: str) -> list[str]:
    m = re.search(rf"^{key}:\s*\n((?:\s*-\s*.+\n?)+)", fm, re.MULTILINE)
    return re.findall(r"^\s*-\s*(.+)$", m.group(1), re.MULTILINE) if m else []


def _linked_items(init_id: str) -> list[dict]:
    """Notes & meetings whose `links` frontmatter includes this initiative id."""
    vault, out = brain._vault(), []
    if not init_id:
        return out
    for kind in ("notes", "meetings"):
        d = vault / kind
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            fm, body = _split(f.read_text())
            if init_id in _fm_list(fm, "links"):
                if _fm_field(fm, "type") == "meeting":
                    body = body.split("## Transcript", 1)[0]
                out.append({"source": f"{kind}/{f.name}",
                            "created": _fm_field(fm, "created")[:10],
                            "text": body.strip()[:1500]})
    return out


def find_initiative(name_or_slug: str) -> Path | None:
    d = brain._vault() / "initiatives"
    if not d.exists():
        return None
    p = d / f"{brain._slugify(name_or_slug)}.md"
    if p.exists():
        return p
    q = name_or_slug.lower()
    for f in sorted(d.glob("*.md")):
        fm, _ = _split(f.read_text())
        if q in _fm_field(fm, "title").lower() or q in f.stem:
            return f
    return None


def list_initiatives() -> list[dict]:
    d = brain._vault() / "initiatives"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.md")):
        fm, _ = _split(f.read_text())
        out.append({
            "slug": f.stem,
            "title": _fm_field(fm, "title") or f.stem,
            "status": _fm_field(fm, "status") or "active",
            "goals": _fm_list(fm, "goals"),
            "linked": len(_linked_items(_fm_field(fm, "id"))),
        })
    return out


INITIATIVE_STATUS_PROMPT = (
    "You are the user's productivity coach reviewing one initiative (a goal or project). "
    "You're given its name and goals plus excerpts from their notes and meetings that relate "
    "to it. Write a concise Markdown status with these sections: a one-line **Momentum** read, "
    "**Recent activity** (bullets, cite the [source]), **Open action items** ('- [ ] …'), and "
    "1-3 **Suggested next steps**. Base everything strictly on the excerpts — don't invent. If "
    "there's little activity, say so and suggest a first concrete step."
)


def initiative_status(name_or_slug: str) -> dict:
    """Synthesize a coach-style status for one initiative from its linked captures."""
    f = find_initiative(name_or_slug)
    if not f:
        return {"title": name_or_slug, "status": f"No initiative matching '{name_or_slug}'.", "linked": 0}
    fm, body = _split(f.read_text())
    title = _fm_field(fm, "title") or f.stem
    items = _linked_items(_fm_field(fm, "id"))
    goals_block = body.split("## Log", 1)[0].strip()

    key = _groq_key()
    if not key:
        return {"title": title, "linked": len(items),
                "status": f"{len(items)} linked capture(s). Set a Groq key for a synthesized status."}
    context = "\n\n".join(f"[{it['source']}] ({it['created']})\n{it['text']}" for it in items) \
        or "(no notes or meetings link to this initiative yet)"

    import httpx
    try:
        resp = httpx.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": ANSWER_MODEL,
                "messages": [
                    {"role": "system", "content": INITIATIVE_STATUS_PROMPT},
                    {"role": "user", "content": f"Initiative: {title}\n\n{goals_block}\n\nLinked captures:\n{context}"},
                ],
                "temperature": 0.3,
                "max_tokens": 700,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return {"title": title, "linked": len(items),
                    "status": resp.json()["choices"][0]["message"]["content"].strip()}
        return {"title": title, "linked": len(items), "status": f"Groq error {resp.status_code}"}
    except Exception as e:
        return {"title": title, "linked": len(items), "status": f"Status error: {e}"}


# --------------------------------------------------------------------------- #
# Dreams: nightly consolidation of captures into initiatives
# --------------------------------------------------------------------------- #
_STATUS_START, _STATUS_END = "<!-- oflow:status -->", "<!-- /oflow:status -->"


def _write_status_snapshot(f: Path, status_md: str, ts: datetime) -> None:
    """Insert/replace a dated status snapshot inside an initiative file. The
    HTML-comment markers make it idempotent and invisible in Obsidian's render."""
    text = f.read_text()
    block = (f"{_STATUS_START}\n## Status — updated {ts:%Y-%m-%d}\n\n"
             f"{status_md.strip()}\n{_STATUS_END}\n")
    if _STATUS_START in text:
        text = re.sub(re.escape(_STATUS_START) + r".*?" + re.escape(_STATUS_END) + r"\n?",
                      block, text, flags=re.DOTALL)
    elif "## Log" in text:
        text = text.replace("## Log", block + "\n## Log", 1)
    else:
        text = text.rstrip() + "\n\n" + block
    f.write_text(text)


def _suggest_initiatives(max_captures: int = 30) -> list[dict]:
    """Look at captures tied to NO initiative; if themes recur, suggest new ones."""
    vault = brain._vault()
    unlinked = []
    for kind in ("notes", "meetings"):
        d = vault / kind
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            fm, body = _split(f.read_text())
            if not _fm_list(fm, "links"):
                if _fm_field(fm, "type") == "meeting":
                    body = body.split("## Transcript", 1)[0]
                unlinked.append(body.strip()[:600])
    key = _groq_key()
    if len(unlinked) < 3 or not key:
        return []
    import httpx
    prompt = (
        "These are the user's recent notes and meetings that aren't tied to any goal or "
        "initiative. Identify up to 3 recurring themes that could each become an initiative "
        "worth tracking — only suggest a theme that shows up across MULTIPLE captures. Respond "
        'ONLY with JSON: {"suggestions": [{"name": "...", "why": "one line"}]}. Empty list if '
        "nothing recurs."
    )
    try:
        resp = httpx.post(
            GROQ_CHAT_URL, headers={"Authorization": f"Bearer {key}"},
            json={"model": ANSWER_MODEL,
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": "\n\n---\n\n".join(unlinked[:max_captures])}],
                  "temperature": 0.4, "max_tokens": 400,
                  "response_format": {"type": "json_object"}},
            timeout=30,
        )
        if resp.status_code == 200:
            return json.loads(resp.json()["choices"][0]["message"]["content"]).get("suggestions", [])
    except Exception as e:
        logger.error(f"Dream suggest error: {e}")
    return []


def _write_dream_journal(linked: int, updated: list, stale: list,
                         suggestions: list, ts: datetime) -> Path:
    lines = [f"Consolidated your brain into your initiatives.\n",
             f"- Re-linked **{linked}** capture(s) to initiatives."]
    if updated:
        lines.append("\n## Initiatives reviewed")
        lines += [f"- **{u['title']}** — {u['linked']} linked capture(s)" for u in updated]
    if stale:
        lines.append("\n## Going quiet")
        lines += [f"- {s} — no linked activity yet" for s in stale]
    if suggestions:
        lines.append("\n## Maybe start an initiative?")
        lines += [f"- **{s.get('name', '')}** — {s.get('why', '')}" for s in suggestions]
    return brain.write_item(
        "dream", "dreams", f"{ts:%Y-%m-%d-%H%M}",
        title=f"Dream — {ts:%Y-%m-%d %H:%M}", body="\n".join(lines) + "\n",
        source="oflow-dream", timestamp=ts,
    )


def dream() -> dict:
    """The consolidation pass: re-link captures, refresh each initiative's status
    snapshot, flag quiet initiatives, suggest emergent ones, and journal it."""
    ts = datetime.now()
    vault = brain._vault()
    linked = link_all()
    inits = list_initiatives()
    updated = []
    for it in inits:
        st = initiative_status(it["slug"])
        f = vault / "initiatives" / f"{it['slug']}.md"
        if f.exists():
            _write_status_snapshot(f, st["status"], ts)
            brain._commit(vault, f, f"dream: refresh status — {it['slug']}")
        updated.append({"title": it["title"], "linked": st["linked"]})
    stale = [it["title"] for it in inits if it["linked"] == 0]
    suggestions = _suggest_initiatives()
    journal = _write_dream_journal(linked, updated, stale, suggestions, ts)
    logger.info(f"Dream: {len(inits)} initiatives refreshed, {linked} re-linked, "
                f"{len(suggestions)} suggestion(s)")
    return {"relinked": linked, "initiatives": len(inits), "stale": stale,
            "suggestions": suggestions, "journal": journal.name}


def main() -> None:
    args = sys.argv[1:]

    # --json emits {"answer","sources"} on stdout (for the UI). Keep logs off
    # stdout so the JSON line is the only thing there.
    as_json = bool(args) and args[0] == "--json"
    if as_json:
        args = args[1:]
    logging.basicConfig(level=logging.WARNING if as_json else logging.INFO, format="%(message)s")

    if not args or args[0] in ("-h", "--help"):
        print('Usage: brain_search.py [--json] "question" | --reindex | --link <file> | --link-all')
        return
    if args[0] == "--reindex":
        n = build_index()
        print(json.dumps({"indexed": n}) if as_json else f"Indexed {n} chunks.")
        return
    if args[0] == "--link":
        titles = link_item(args[1]) if len(args) > 1 else []
        print(json.dumps({"linked": titles}) if as_json else f"Linked to: {titles or 'none'}")
        return
    if args[0] == "--link-all":
        n = link_all()
        print(json.dumps({"linked": n}) if as_json else f"Linked {n} items to initiatives.")
        return
    if args[0] == "--initiatives":
        data = list_initiatives()
        print(json.dumps(data) if as_json
              else ("\n".join(f"- {i['title']} ({i['linked']} linked)" for i in data) or "No initiatives yet."))
        return
    if args[0] == "--initiative":
        data = initiative_status(" ".join(args[1:]))
        print(json.dumps(data) if as_json else f"# {data['title']}\n\n{data['status']}")
        return
    if args[0] == "--dream":
        r = dream()
        print(json.dumps(r) if as_json else
              f"Dreamt: {r['initiatives']} initiative(s) refreshed, {r['relinked']} re-linked, "
              f"{len(r['suggestions'])} suggestion(s). Journal: dreams/{r['journal']}")
        return

    query = " ".join(args)
    text, sources = answer(query)
    if as_json:
        print(json.dumps({"answer": text, "sources": sources}))
    else:
        print(text)
        if sources:
            print("\nSources: " + ", ".join(sources))


if __name__ == "__main__":
    main()
