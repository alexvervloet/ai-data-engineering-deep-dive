"""Lesson 2: join a consistent snapshot to CDC without a gap."""

from ai_data.connectors import MemoryConnector

from _fixtures import record


def main() -> None:
    connector = MemoryConnector()
    connector.upsert(record("deploy.md"))

    snapshot, cursor = connector.capture_snapshot()
    print(f"SNAPSHOT @ cursor {cursor}: {[item.external_id for item in snapshot]}")

    # This write happens after the snapshot. Starting CDC at zero would duplicate
    # the snapshot; starting after a later cursor could lose it.
    connector.upsert(record("on-call.md"))
    connector.upsert(record("deploy.md", version=2, text="Now requires three reviewers."))

    first_page = connector.changes_after(cursor, limit=1)
    second_page = connector.changes_after(first_page.next_cursor, limit=1)
    print("\nCDC AFTER SNAPSHOT")
    for page in (first_page, second_page):
        event = page.events[0]
        print(
            f"  sequence={event.sequence} {event.kind.value} "
            f"{event.external_id} v{event.version}"
        )

    seen = {item.external_id for item in snapshot}
    seen.update(event.external_id for event in (*first_page.events, *second_page.events))
    print(f"\nNo gap: {sorted(seen)}")
    print("Takeaway: capture the source watermark with the snapshot, then consume after it.")


if __name__ == "__main__":
    main()
