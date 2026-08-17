"""
Shared source records for the ten runnable lessons.

Every lesson in this dive is about what happens to a document over time: it gets
parsed, versioned, re-shared, updated, deleted, replayed, backfilled, and rebuilt.
That story only reads clearly if the document itself is boring and identical every
time, so the interesting thing on screen is the pipeline's behavior rather than the
content.

Hence one small fixture factory, with three properties worth noticing:

**Deterministic.** `updated_at` is a fixed timestamp, not `datetime.now()`. If the
fixtures moved with the clock, two runs would produce different output and the
lessons could not tell you what to expect before you run them. Anything in a
teaching example that changes between runs should be changing because the lesson is
about it.

**Valid by default.** The record returned here passes the contract in
`ai_data.contracts` as-is. Lessons that need a rejection build the invalid case
explicitly, so an error on screen is always a deliberate one.

**Overridable one field at a time.** Each lesson varies exactly the field it is about
(the version, the ACL, the tenant, the MIME type, the text) and leaves the rest
alone. When output differs between two calls, the difference has one cause.

This file is not part of the `ai_data` package. It is example scaffolding, and the
leading underscore keeps it out of the numbered lesson sequence.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ai_data.models import AccessControl, SourceRecord

# A fixed instant, so the fixtures do not move with the wall clock. Real connectors
# use the timestamp the source system reports, which is why the field exists at all:
# it is evidence about the source, not about when this pipeline happened to run.
FIXED_SOURCE_TIME = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)


def record(
    external_id: str,
    *,
    tenant: str = "acme",
    version: int = 1,
    text: str = "Deployments require two reviewers.\n\nRollback within fifteen minutes.",
    readers: frozenset[str] = frozenset({"user:alex", "group:engineering"}),
    mime_type: str = "text/markdown",
) -> SourceRecord:
    """Build one valid source record, varying only what a lesson asks for.

    The default text is two paragraphs on purpose. One paragraph would chunk to a
    single chunk, and several lessons need to show chunks being replaced, counted,
    and re-embedded as a group.
    """

    return SourceRecord(
        tenant_id=tenant,
        external_id=external_id,
        version=version,
        updated_at=FIXED_SOURCE_TIME,
        source_uri=f"file:///{tenant}/{external_id}",
        mime_type=mime_type,
        content=text.encode("utf-8"),
        acl=AccessControl(readers),
        metadata={"department": "engineering"},
    )
