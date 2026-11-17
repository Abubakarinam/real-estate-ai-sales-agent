"""Knowledge base: reads the agency's documents (Markdown, text, PDF) and finds the
best passages for a question with BM25 keyword ranking.

BM25 needs no embedding model, no vector database and no extra API key, so the demo
runs anywhere. For thousands of documents, swap `search()` for a vector store
(Supabase pgvector, Chroma, etc.); the agent only depends on its return shape.
"""
import math
import re
from collections import Counter
from pathlib import Path

from . import config, db

SUPPORTED = {".md", ".txt", ".pdf"}
MAX_CHUNK_CHARS = 1100

_STOP = set(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or our "
    "so that the their there this to us was we what when where which who will with you your am any "
    "about into than then them they these those would could should".split()
)
# Arabic: strip the definite article and common prefixes so "العقار" matches "عقار"
_AR_PREFIX = re.compile(r"^(وال|بال|كال|فال|لل|ال)")
_TOKEN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP or len(tok) < 2:
            continue
        if re.match(r"[؀-ۿ]", tok):
            tok = _AR_PREFIX.sub("", tok) or tok
        elif len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


def read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def chunk(text: str, source: str) -> list[tuple[str, str]]:
    """Split by Markdown headings, then by paragraphs so each chunk stays small."""
    sections: list[tuple[str, list[str]]] = []
    heading = Path(source).stem
    lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            if any(l.strip() for l in lines):
                sections.append((heading, lines))
            heading, lines = m.group(1).strip(), []
        else:
            lines.append(line)
    if any(l.strip() for l in lines):
        sections.append((heading, lines))

    chunks: list[tuple[str, str]] = []
    for head, body_lines in sections:
        body = "\n".join(body_lines).strip()
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            if buf and len(buf) + len(para) > MAX_CHUNK_CHARS:
                chunks.append((head, buf.strip()))
                buf = ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append((head, buf.strip()))
    return chunks


def ingest_file(path: Path) -> int:
    text = read_file(path)
    pieces = chunk(text, path.name)
    with db.tx() as c:
        c.execute("DELETE FROM knowledge_chunks WHERE source=?", (path.name,))
        c.executemany(
            "INSERT INTO knowledge_chunks(source, heading, text) VALUES (?,?,?)",
            [(path.name, h, t) for h, t in pieces],
        )
    _cache.clear()
    return len(pieces)


def ingest_folder(folder: Path | None = None) -> dict[str, int]:
    folder = folder or config.KNOWLEDGE_DIR
    result = {}
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() in SUPPORTED:
            result[path.name] = ingest_file(path)
    return result


def ensure_loaded() -> None:
    if not db.one("SELECT id FROM knowledge_chunks LIMIT 1"):
        ingest_folder()


def delete_source(source: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM knowledge_chunks WHERE source=?", (source,))
    _cache.clear()


def sources() -> list[dict]:
    return db.query(
        "SELECT source, COUNT(*) AS chunks, SUM(LENGTH(text)) AS chars FROM knowledge_chunks "
        "GROUP BY source ORDER BY source"
    )


_cache: dict = {}


def _index():
    if "docs" not in _cache:
        rows = db.query("SELECT id, source, heading, text FROM knowledge_chunks")
        docs = [(r, tokenize(f"{r['heading']} {r['heading']} {r['text']}")) for r in rows]
        df = Counter()
        for _, toks in docs:
            df.update(set(toks))
        avg = sum(len(t) for _, t in docs) / max(len(docs), 1)
        _cache.update(docs=docs, df=df, avg=avg)
    return _cache["docs"], _cache["df"], _cache["avg"]


def search(query: str, top_k: int = 4) -> list[dict]:
    docs, df, avg = _index()
    n = len(docs)
    q = tokenize(query)
    if not q or not n:
        return []
    k1, b = 1.5, 0.75
    scored = []
    for row, toks in docs:
        tf = Counter(toks)
        s = 0.0
        for term in set(q):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(toks) / avg))
        if s > 0:
            scored.append((s, row))
    scored.sort(key=lambda x: -x[0])
    return [
        {"source": r["source"], "heading": r["heading"], "text": r["text"], "score": round(s, 2)}
        for s, r in scored[:top_k]
    ]
