"""
Lesson 2: join a consistent snapshot to a change feed without a gap.

Getting documents out of a source system looks like two features: crawl everything
once, then keep up with changes. They are not two features. They are one protocol,
and the seam between them is where corpora go wrong unnoticed.

The protocol has four steps:

  1. capture the source's high-watermark (its "you are here" in the change history);
  2. read a snapshot at that same logical instant;
  3. consume changes strictly after that watermark;
  4. persist the new cursor only after the index write commits.

This example makes steps 1 to 3 visible. A document is written, a snapshot is taken,
and then two more changes land after it. Watch which events the change feed returns
and convince yourself that nothing is missed and nothing is done twice.

The failure modes are asymmetric, which is the real lesson:

  - Start the change feed **too early** (cursor 0 here) and you reprocess documents
    the snapshot already contained. That costs money and nothing else, because
    version-aware writes turn a duplicate into a no-op (lesson 6).
  - Start it **too late** (cursor 2 here) and the documents that changed during your
    crawl are never seen again by anything, until a user notices the answer is stale.

Given a choice between paying twice and losing data invisibly, overlap the window.
Design pipelines so the cheap failure is the one that happens.

One production note this example cannot show because its cursors are readable
integers: treat a real provider's cursor as an **opaque token**. It may be a
timestamp, a log sequence number, a page token, or vendor JSON. The moment your code
adds a second to it or compares two of them, you have taken a dependency the provider
never offered.

Predict before running: which events come back after the snapshot cursor, and in
what order? Then try cursor 0 and cursor 2 and confirm the two failure modes.

Previous: lesson 1 validated a single record. Next: lesson 3 turns bytes into text.
See README section 2 and TEXTBOOK.md section 19.2.

Run it:

    python examples/02_connectors_and_cursors.py
"""

from ai_data.connectors import MemoryConnector

from _fixtures import record


def main() -> None:
    connector = MemoryConnector()
    connector.upsert(record("deploy.md"))

    # Step 1 and 2 together, and the fact that they are one call is the point: the
    # snapshot and the cursor describe the same logical instant. Reading state at one
    # moment and taking a watermark at another leaves a window with no owner.
    snapshot, cursor = connector.capture_snapshot()
    print(f"SNAPSHOT @ cursor {cursor}: {[item.external_id for item in snapshot]}")

    # These writes happen after the snapshot. Starting CDC at zero would duplicate
    # the snapshot; starting after a later cursor could lose it.
    connector.upsert(record("on-call.md"))
    connector.upsert(record("deploy.md", version=2, text="Now requires three reviewers."))

    # Step 3, one page at a time. Pagination is not a detail: a real backlog does not
    # arrive in one response, and the cursor has to survive between pages as well as
    # between runs.
    first_page = connector.changes_after(cursor, limit=1)
    second_page = connector.changes_after(first_page.next_cursor, limit=1)
    print("\nCDC AFTER SNAPSHOT")
    for page in (first_page, second_page):
        event = page.events[0]
        print(
            f"  sequence={event.sequence} {event.kind.value} "
            f"{event.external_id} v{event.version}"
        )

    # Snapshot plus changes covers every document exactly once. That is the property
    # the four-step protocol exists to produce.
    seen = {item.external_id for item in snapshot}
    seen.update(event.external_id for event in (*first_page.events, *second_page.events))
    print(f"\nNo gap: {sorted(seen)}")
    print("Takeaway: capture the source watermark with the snapshot, then consume after it.")


if __name__ == "__main__":
    main()
