"""Разбор референсов методиста (.docx, .pdf, .txt).

Из референса берём только структуру: разделы и учебный план. Дословный текст
в генерацию не передаётся целиком — это требование кейса о недопустимости
копирования чужих курсов.
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_CHARS = 6000


def parse_docx(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"не похоже на .docx: {exc}") from exc
    root = ET.fromstring(xml)
    body = root.find(f"{W}body")
    if body is None:
        return ""
    chunks: list[str] = []
    for node in body:
        tag = node.tag.replace(W, "")
        if tag == "p":
            text = "".join(t.text or "" for t in node.iter(f"{W}t")).strip()
            if text:
                chunks.append(text)
        elif tag == "tbl":
            for row in node.findall(f"{W}tr"):
                cells = [
                    "".join(t.text or "" for t in cell.iter(f"{W}t")).strip()
                    for cell in row.findall(f"{W}tc")
                ]
                if any(cells):
                    chunks.append(" | ".join(cells))
    return "\n".join(chunks)


def parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError("для .pdf нужен пакет pypdf") from exc
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_reference(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".docx"):
        text = parse_docx(data)
    elif name.endswith(".pdf"):
        text = parse_pdf(data)
    else:
        text = data.decode("utf-8", errors="replace")
    return summarize_structure(text)


def summarize_structure(text: str) -> str:
    """Сжать референс до структуры: заголовки разделов и строки учебного плана."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    keep: list[str] = []
    for line in lines:
        is_heading = (
            line[:1].isdigit()
            and ("." in line[:6])
            or line.isupper()
            or line.startswith(("Модуль", "Тема", "Раздел", "Приложение"))
        )
        is_plan_row = "|" in line
        if is_heading or is_plan_row:
            keep.append(line[:300])
        if sum(len(x) for x in keep) > MAX_CHARS:
            keep.append("… (референс усечён)")
            break
    return "\n".join(keep)
