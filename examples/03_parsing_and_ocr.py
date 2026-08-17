"""
Lesson 3: parsing is a versioned transform, and OCR is an explicit adapter.

This is the least glamorous code in the pipeline and, per hour invested, it decides
more of your quality than any retrieval parameter. Real corpora are PDFs with
two-column layouts, scans that need OCR, HTML wrapped in navigation chrome, and
exports whose tables become word salad. Everything downstream inherits whatever comes
out of here.

Two fields exist for one future moment: the day you change the parser.

  - `parser_version` tells you which documents were processed by the code that had
    the bug. Without it, a fix means re-processing everything and hoping.
  - `content_hash` tells you whether re-parsing actually changed the text. Without
    it, a parser upgrade re-embeds the whole corpus, including the ninety percent
    that came out byte-identical.

An HTML cleanup, a PDF library upgrade, an OCR model swap, or a Unicode
normalization fix can each change the text of every document, which changes every
chunk, which invalidates every embedding. That is a backfill (lesson 7), and these
two fields are what make it decidable rather than total.

The example also shows the OCR seam **failing closed**. A PDF with no adapter raises
rather than parsing to an empty string. Indexing empty text is the worst available
outcome: the document exists, retrieval never returns it, and nothing errors, so the
failure surfaces as a user asking why the assistant does not know about a file they
can see with their own eyes. An error at ingest is a bad afternoon; a silent empty
parse is a bad quarter.

Not visible in the output, but worth knowing: the HTML path drops `script`, `style`,
`template`, and `noscript` bodies. That is partly hygiene, since minified CSS makes
poor context. It is also the ingest end of prompt injection, because script text is
arbitrary text on a page you did not write, and whatever the parser keeps eventually
reaches a model's context window.

Predict before running: what exactly does the HTML become, and does the hash change
if you only reorder whitespace?

Previous: lesson 2 fetched the bytes. Next: lesson 4 gives the text an identity.
See README section 3 and TEXTBOOK.md section 19.4.

Run it:

    python examples/03_parsing_and_ocr.py
"""

from ai_data.parsing import ParseError, parse_document

from _fixtures import record


def fake_ocr(content: bytes, mime_type: str) -> str:
    """Stand in for a real OCR service, which is a network call with a bill attached.

    The signature is the interesting part. A production adapter takes bytes and a
    MIME type and returns text, so it can be a local library, a cloud API, or a
    queue, without the pipeline knowing which. Whatever it is, it needs its own
    version recorded, because swapping it changes text just as a parser change does.
    """

    print(f"  OCR adapter received {len(content)} bytes as {mime_type}")
    return "Scanned runbook: rotate credentials every ninety days."


def main() -> None:
    # HTML in, plain text out. The tags carry structure that matters for chunking,
    # and nothing else that belongs in a vector.
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

    # A PDF with no OCR adapter configured. The pipeline refuses rather than indexing
    # a document with no text, because the second failure is invisible.
    scanned = record("scan.pdf", mime_type="application/pdf", text="%PDF-fake")
    print("\nPDF WITHOUT OCR")
    try:
        parse_document(scanned)
    except ParseError as exc:
        print(f"  rejected: {exc}")

    # The same document with the seam filled in. Nothing else about the pipeline
    # changes, which is what makes this a seam rather than a special case.
    print("\nPDF WITH OCR")
    parsed_scan = parse_document(scanned, ocr=fake_ocr)
    print(f"  text: {parsed_scan.text}")
    print("\nTakeaway: store parser/OCR versions so a backfill can explain changed text.")


if __name__ == "__main__":
    main()
