"""Lesson 3: parsing is a versioned transform; OCR is an explicit adapter."""

from ai_data.parsing import ParseError, parse_document

from _fixtures import record


def fake_ocr(content: bytes, mime_type: str) -> str:
    print(f"  OCR adapter received {len(content)} bytes as {mime_type}")
    return "Scanned runbook: rotate credentials every ninety days."


def main() -> None:
    html = record(
        "runbook.html",
        mime_type="text/html",
        text="<h1>Runbook</h1><p>Rollback within fifteen minutes.</p>",
    )
    parsed_html = parse_document(html)
    print("HTML")
    print(f"  text: {parsed_html.text}")
    print(f"  parser: {parsed_html.parser_version}")
    print(f"  hash: {parsed_html.content_hash[:16]}...")

    scanned = record("scan.pdf", mime_type="application/pdf", text="%PDF-fake")
    print("\nPDF WITHOUT OCR")
    try:
        parse_document(scanned)
    except ParseError as exc:
        print(f"  rejected: {exc}")

    print("\nPDF WITH OCR")
    parsed_scan = parse_document(scanned, ocr=fake_ocr)
    print(f"  text: {parsed_scan.text}")
    print("\nTakeaway: store parser/OCR versions so a backfill can explain changed text.")


if __name__ == "__main__":
    main()
