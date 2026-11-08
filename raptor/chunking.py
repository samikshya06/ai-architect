import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]


@dataclass
class Node:
    """A node in the RAPTOR tree: a leaf chunk (level 0) or a cluster summary."""

    id: str
    text: str
    level: int
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None


def _node_id(source: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}:{index}:{text}".encode()).hexdigest()[:16]
    return f"L0-{digest}"


def _title_of(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def chunk_markdown(
    path: Path, chunk_size: int = 1200, chunk_overlap: int = 100
) -> list[Node]:
    """Split by markdown headers; only oversized sections are split further by size."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    header_splitter = MarkdownHeaderTextSplitter(HEADERS, strip_headers=False)
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    docs = size_splitter.split_documents(header_splitter.split_text(text))

    doc_title = _title_of(text, path.stem)
    nodes: list[Node] = []
    for i, doc in enumerate(docs):
        content = doc.page_content.strip()
        if not content:
            continue
        header_path = " > ".join(
            doc.metadata[key] for _, key in HEADERS if key in doc.metadata
        )
        nodes.append(
            Node(
                id=_node_id(path.name, i, content),
                text=content,
                level=0,
                metadata={
                    "source": path.name,
                    "doc_title": doc_title,
                    "header_path": header_path,
                    **doc.metadata,
                    "chunk_index": i,
                    "is_summary": False,
                },
            )
        )
    return nodes


def chunk_directory(
    docs_dir: Path, chunk_size: int = 1200, chunk_overlap: int = 100
) -> list[Node]:
    nodes: list[Node] = []
    for path in sorted(docs_dir.glob("*.md")):
        nodes.extend(chunk_markdown(path, chunk_size, chunk_overlap))
    return nodes
