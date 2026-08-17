"""Markdown → retrieval chunks, per the authoring contract in knowledge/README.md.

- YAML front-matter (title, doc_type, tags) — parsed with a tiny bespoke
  parser (flat scalars + inline lists only; no yaml dependency).
- Heading-based splitting on `##`/`###`: each section is self-contained by
  authoring rule, so a section = a chunk. `###` chunks carry an "H2 › H3"
  breadcrumb so citations read well on their own.
- Deterministic content_hash lets ingestion skip unchanged chunks (no
  needless re-embedding).
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_CHUNK_CHARS = 4000  # sanity guard; sections are far smaller in practice


class KnowledgeFormatError(ValueError):
    """Document violates the knowledge/README.md authoring contract."""


@dataclass(frozen=True)
class DocMeta:
    title: str
    doc_type: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Chunk:
    doc_path: str  # relative to knowledge/, e.g. "menu-guide/dosas.md"
    doc_type: str
    title: str
    tags: tuple[str, ...]
    heading: str  # breadcrumb, e.g. "Dosa Guide › Masala Dosa — ₹120"
    chunk_index: int
    content: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        digest = hashlib.sha256(
            f"{self.doc_path}\n{self.heading}\n{self.content}".encode()
        ).hexdigest()
        object.__setattr__(self, "content_hash", digest)


def parse_front_matter(text: str, *, doc_path: str) -> tuple[DocMeta, str]:
    """Parse the YAML front-matter block; returns (meta, body)."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        raise KnowledgeFormatError(f"{doc_path}: missing front-matter block")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip() != line:  # skip blanks/continuations
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    title, doc_type = fields.get("title", ""), fields.get("doc_type", "")
    if not title or not doc_type:
        raise KnowledgeFormatError(f"{doc_path}: front-matter needs title and doc_type")
    raw_tags = fields.get("tags", "")
    tags = tuple(
        t.strip() for t in raw_tags.strip("[]").split(",") if t.strip()
    )  # inline list only
    return DocMeta(title=title, doc_type=doc_type, tags=tags), text[match.end() :]


def _sections(body: str) -> list[tuple[str, str, str]]:
    """Split on ##/### → list of (h2, h3, content). Preamble → ("", "", text)."""
    out: list[tuple[str, str, str]] = []
    h2 = h3 = ""
    lines: list[str] = []

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            out.append((h2, h3, content))
        lines.clear()

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            h2, h3 = line[3:].strip(), ""
        elif line.startswith("### "):
            flush()
            h3 = line[4:].strip()
        else:
            lines.append(line)
    flush()
    return out


def chunk_document(doc_path: str, text: str) -> list[Chunk]:
    """Chunk one markdown document. Chunk indexes are stable top-to-bottom."""
    meta, body = parse_front_matter(text, doc_path=doc_path)
    body = re.sub(r"^# .*\n", "", body, count=1, flags=re.MULTILINE)  # drop H1 (dup of title)

    chunks: list[Chunk] = []
    for h2, h3, content in _sections(body):
        breadcrumb = " › ".join(p for p in (meta.title, h2, h3) if p)
        if len(content) > _MAX_CHUNK_CHARS:
            raise KnowledgeFormatError(
                f"{doc_path}: section '{breadcrumb}' exceeds {_MAX_CHUNK_CHARS} chars — "
                "split it with subheadings (authoring rule: self-contained sections)"
            )
        chunks.append(
            Chunk(
                doc_path=doc_path,
                doc_type=meta.doc_type,
                title=meta.title,
                tags=meta.tags,
                heading=breadcrumb,
                chunk_index=len(chunks),
                content=content,
            )
        )
    if not chunks:
        raise KnowledgeFormatError(f"{doc_path}: no content sections found")
    return chunks


def load_knowledge_dir(knowledge_dir: Path) -> list[Chunk]:
    """Chunk every markdown doc under knowledge/ (README is not knowledge)."""
    chunks: list[Chunk] = []
    for path in sorted(knowledge_dir.rglob("*.md")):
        if path.name == "README.md":
            continue
        rel = path.relative_to(knowledge_dir).as_posix()
        chunks.extend(chunk_document(rel, path.read_text()))
    return chunks
