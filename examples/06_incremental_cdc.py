"""Lesson 6: incremental updates are ordered, checkpointed, and replay-safe."""

from ai_data.catalog import InMemoryCatalog
from ai_data.connectors import MemoryConnector
from ai_data.embedding import DeterministicEmbedder
from ai_data.pipeline import SyncPipeline

from _fixtures import record


def main() -> None:
    connector = MemoryConnector()
    connector.upsert(record("deploy.md"))
    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    pipeline = SyncPipeline(connector, catalog, embedder)
    bootstrap = pipeline.bootstrap()
    checkpoint = bootstrap.end_cursor
    print(f"BOOTSTRAP: cursor {checkpoint}, documents={len(catalog.documents)}")

    connector.upsert(record("deploy.md", version=2, text="Deploy with three reviewers."))
    connector.upsert(record("on-call.md", text="Escalate after five minutes."))
    connector.delete("acme", "on-call.md", version=2)

    print("\nCDC PAGES")
    for run in pipeline.drain(limit=2):
        statuses = [report.status for report in run.reports]
        print(f"  {run.start_cursor}->{run.end_cursor}: {statuses}")

    print("\nREPLAY AFTER CRASHED CHECKPOINT WRITE")
    replay = SyncPipeline(connector, catalog, embedder, cursor=checkpoint)
    replay_statuses = [
        report.status for run in replay.drain(limit=10) for report in run.reports
    ]
    print(f"  replay statuses: {replay_statuses}")
    print(f"  deploy version: {catalog.documents[('acme', 'deploy.md')].source_version}")
    print(f"  on-call deleted: {catalog.documents[('acme', 'on-call.md')].deleted}")
    print("\nTakeaway: persist the CDC cursor only after the atomic index write commits.")


if __name__ == "__main__":
    main()
