from __future__ import annotations

from pathlib import Path
import unicodedata

from docx import Document
from pypdf import PdfReader


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}
MAX_INDEXED_TEXT_CHARS = 1_000_000


class ExtractionError(RuntimeError):
    """A document could not be read or parsed for local search."""


class UnsupportedDocumentError(ExtractionError):
    """The document extension is outside the supported search formats."""


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _bounded_piece(text: str, remaining: int) -> str:
    if remaining <= 0:
        return ""
    return _normalize(text)[:remaining]


def _extract_plain_text(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8-sig") as document:
            raw_text = document.read(MAX_INDEXED_TEXT_CHARS)
    except (OSError, UnicodeError) as error:
        raise ExtractionError(f"Could not read document '{path}': {error}") from error
    return _bounded_piece(raw_text, MAX_INDEXED_TEXT_CHARS)


def _extract_pdf(path: Path) -> str:
    pieces: list[str] = []
    length = 0
    try:
        reader = PdfReader(path)
        for page in reader.pages:
            remaining = MAX_INDEXED_TEXT_CHARS - length
            if remaining <= 0:
                break
            page_text = page.extract_text() or ""
            piece = _bounded_piece(page_text, remaining)
            if piece:
                pieces.append(piece)
                length += len(piece)
    except Exception as error:
        raise ExtractionError(f"Could not extract PDF '{path}': {error}") from error
    return " ".join(pieces)[:MAX_INDEXED_TEXT_CHARS]


def _extract_docx(path: Path) -> str:
    pieces: list[str] = []
    length = 0
    try:
        document = Document(path)
        text_blocks = (paragraph.text for paragraph in document.paragraphs)
        for text in text_blocks:
            remaining = MAX_INDEXED_TEXT_CHARS - length
            if remaining <= 0:
                break
            piece = _bounded_piece(text, remaining)
            if piece:
                pieces.append(piece)
                length += len(piece)

        if length < MAX_INDEXED_TEXT_CHARS:
            for table in document.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            remaining = MAX_INDEXED_TEXT_CHARS - length
                            if remaining <= 0:
                                break
                            piece = _bounded_piece(paragraph.text, remaining)
                            if piece:
                                pieces.append(piece)
                                length += len(piece)
                        if length >= MAX_INDEXED_TEXT_CHARS:
                            break
                    if length >= MAX_INDEXED_TEXT_CHARS:
                        break
                if length >= MAX_INDEXED_TEXT_CHARS:
                    break
    except Exception as error:
        raise ExtractionError(f"Could not extract DOCX '{path}': {error}") from error
    return " ".join(pieces)[:MAX_INDEXED_TEXT_CHARS]


def extract_text(path: Path) -> str:
    """Extract normalized, bounded searchable text from a supported document."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedDocumentError(
            f"Unsupported document type '{suffix or '[no extension]'}' for '{path}'"
        )

    if suffix in {".txt", ".md"}:
        return _extract_plain_text(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    return _extract_docx(path)