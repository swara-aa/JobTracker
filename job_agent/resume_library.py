from __future__ import annotations

from io import BytesIO
from pathlib import Path

from werkzeug.datastructures import FileStorage


MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_RESUME_TEXT = 30000
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt"}


def extract_resume(file: FileStorage) -> tuple[str, str]:
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"{filename or 'File'} must be a PDF, DOCX, or TXT file.")

    raw = file.read(MAX_RESUME_BYTES + 1)
    if len(raw) > MAX_RESUME_BYTES:
        raise ValueError(f"{filename} is larger than 5 MB.")
    if not raw:
        raise ValueError(f"{filename} is empty.")

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix == ".docx":
        from docx import Document

        document = Document(BytesIO(raw))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")

    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) < 100:
        raise ValueError(
            f"Could not extract enough text from {filename}. Scanned PDFs need OCR first."
        )
    return filename, text[:MAX_RESUME_TEXT]
