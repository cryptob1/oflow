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
LINK_THRESHOLD = float(os.environ.get("OFLOW_LINK_THRESHOLD", "0.5"))
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
