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
import sys

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
    for kind in ("notes", "meetings"):
        d = vault / kind
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            for ch in _chunk(f.read_text()):
                docs.append({"source": f"{kind}/{f.name}", "text": ch})
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print('Usage: brain_search.py "your question"   |   --reindex')
        return
    if args[0] == "--reindex":
        n = build_index()
        print(f"Indexed {n} chunks.")
        return
    query = " ".join(args)
    text, sources = answer(query)
    print(text)
    if sources:
        print("\nSources: " + ", ".join(sources))


if __name__ == "__main__":
    main()
