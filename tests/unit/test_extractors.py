from __future__ import annotations

from pathlib import Path

import pytest

from personal_assistant.search.extractors import (
    MAX_INDEXED_TEXT_CHARS,
    SUPPORTED_SUFFIXES,
    ExtractionError,
    UnsupportedDocumentError,
    extract_text,
)


@pytest.fixture
def document_factory(request):
    created_paths: list[Path] = []

    def create(suffix: str) -> Path:
        path = (
            Path.cwd()
            / ".tmp"
            / f"test-extractors-{request.node.name}{suffix}"
        )
        path.unlink(missing_ok=True)
        created_paths.append(path)
        return path

    yield create

    for path in created_paths:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_extracts_utf8_text_and_markdown(document_factory, suffix):
    path = document_factory(suffix)
    path.write_text("Café notes\n\nA second paragraph.", encoding="utf-8")

    extracted = extract_text(path)

    assert suffix in SUPPORTED_SUFFIXES
    assert "Café notes" in extracted
    assert "A second paragraph." in extracted


def test_extracts_generated_pdf(document_factory):
    from reportlab.pdfgen import canvas

    path = document_factory(".pdf")
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "Synthetic PDF searchable phrase")
    pdf.save()

    extracted = extract_text(path)

    assert ".pdf" in SUPPORTED_SUFFIXES
    assert "Synthetic PDF searchable phrase" in extracted


def test_extracts_generated_docx(document_factory):
    from docx import Document

    path = document_factory(".docx")
    document = Document()
    document.add_paragraph("Synthetic DOCX searchable phrase")
    document.save(path)

    extracted = extract_text(path)

    assert ".docx" in SUPPORTED_SUFFIXES
    assert "Synthetic DOCX searchable phrase" in extracted


def test_rejects_unsupported_extensions(document_factory):
    path = document_factory(".rtf")
    path.write_text("This format is outside the MVP.", encoding="utf-8")

    with pytest.raises(UnsupportedDocumentError):
        extract_text(path)


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        (".pdf", b"not a valid PDF"),
        (".docx", b"not a valid DOCX package"),
    ],
    ids=["corrupt-pdf", "corrupt-docx"],
)
def test_corrupt_documents_raise_a_per_file_extraction_error(
    document_factory, suffix, payload
):
    path = document_factory(suffix)
    path.write_bytes(payload)

    with pytest.raises(ExtractionError):
        extract_text(path)


def test_caps_extracted_content_at_the_index_limit(document_factory):
    path = document_factory(".txt")
    path.write_text("x" * (MAX_INDEXED_TEXT_CHARS + 100), encoding="utf-8")

    extracted = extract_text(path)

    assert len(extracted) == MAX_INDEXED_TEXT_CHARS
