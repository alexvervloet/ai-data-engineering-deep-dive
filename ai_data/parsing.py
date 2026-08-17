"""Parsing and OCR as versioned, replaceable transformations."""

from __future__ import annotations

from collections.abc import Callable
from html.parser import HTMLParser

from .identity import normalize_text, text_hash
from .models import ParsedDocument, SourceRecord

PARSER_VERSION = "parser-v1"
OCR = Callable[[bytes, str], str]


class ParseError(ValueError):
    pass


class _HTMLTextExtractor(HTMLParser):
    _BLOCKS = {"article", "br", "div", "h1", "h2", "h3", "li", "p", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError("text content is not valid UTF-8") from exc


def _html_to_text(content: bytes) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(_decode_utf8(content))
    return " ".join("".join(parser.parts).split())


def parse_document(record: SourceRecord, *, ocr: OCR | None = None) -> ParsedDocument:
    if record.mime_type in {"text/plain", "text/markdown"}:
        extracted = _decode_utf8(record.content)
    elif record.mime_type == "text/html":
        extracted = _html_to_text(record.content)
    elif record.mime_type in {"application/pdf", "image/png"}:
        if ocr is None:
            raise ParseError(f"{record.mime_type} requires an OCR adapter")
        extracted = ocr(record.content, record.mime_type)
    else:
        raise ParseError(f"no parser registered for {record.mime_type}")

    text = normalize_text(extracted)
    if not text:
        raise ParseError("parser produced empty text")
    return ParsedDocument(
        tenant_id=record.tenant_id,
        external_id=record.external_id,
        version=record.version,
        source_uri=record.source_uri,
        text=text,
        content_hash=text_hash(text),
        acl=record.acl,
        metadata=dict(record.metadata),
        parser_version=PARSER_VERSION,
    )
