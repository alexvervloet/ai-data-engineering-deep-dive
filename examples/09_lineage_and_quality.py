"""Lesson 9: derived data needs lineage and quality gates of its own."""

from ai_data.catalog import InMemoryCatalog
from ai_data.embedding import DeterministicEmbedder
from ai_data.quality import QualityReport, assess_quality

from _fixtures import record


def print_report(label: str, report: QualityReport) -> None:
    print(label)
    for check in report.checks:
        marker = "PASS" if check.passed else "FAIL"
        print(f"  {marker:4} {check.name}: {check.value:.3f} ({check.expectation})")


def main() -> None:
    records = (record("deploy.md"), record("on-call.md", text="Escalate in five minutes."))
    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    for source in records:
        catalog.upsert(source, embedder)

    report = assess_quality(records, catalog)
    print_report("HEALTHY PIPELINE", report)
    edge = next(iter(catalog.lineage.values()))
    print(
        f"\nLINEAGE: {edge.parent_id} -> {edge.child_id} "
        f"via {edge.transform}@{edge.transform_version}"
    )

    catalog.lineage.pop(edge.child_id)
    broken = assess_quality(records, catalog)
    print()
    print_report("AFTER A LOST LINEAGE WRITE", broken)
    print(f"\nRelease gate passes: {broken.ok}")
    print("Takeaway: validate coverage, ACLs, lineage, dimensions, and drift before promotion.")


if __name__ == "__main__":
    main()
