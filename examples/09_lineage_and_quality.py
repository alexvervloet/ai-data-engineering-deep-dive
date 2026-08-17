"""
Lesson 9: derived data needs lineage and quality gates of its own.

The evals dive made quality a number you can rerun. The observability dive made it a
trend you watch. Both measure the system's **answers**, and neither can see the
failures in this dive. Be precise about why: a stale corpus scores perfectly against
a stale eval set. The retrieval was accurate, the ranking was good, and the document
was five months out of date. No answer-quality metric contains the information needed
to notice.

So there is an earlier gate that checks the corpus rather than the answers: source
coverage, reconciliation drift, empty chunks, ACL parity between documents and their
chunks, chunks with an owning document, lineage coverage, embedding dimensions,
duplicate ratios, and active documents that somehow have no chunks.

Each check measures one thing on purpose. That is the same diagnostic principle as
the RAG dive's split between retrieval and generation metrics: when a gate fails, the
check that moved should name the stage that broke. This example proves it by breaking
exactly one thing, a single lineage edge, and showing exactly one check go red while
every other stays green. A single "data health" score would have told you that
something, somewhere, is wrong.

Note the two kinds of check. Most are critical and block a release. The duplicate
ratio is not, because a corpus legitimately contains near-identical documents (last
quarter's policy, the same boilerplate in twelve contracts) and a gate that fires on
normal data gets muted, which is worse than not having it.

**Lineage** is the quiet piece that makes the rest debuggable. Every chunk records
which document produced it, through which transform, at which version. When someone
asks why the assistant said something strange, lineage turns that question into a
query instead of an afternoon.

One design detail that cost this repository a bug: a gate has to survive the
corruption it exists to detect. The ACL check used to read each chunk's ACL through
its document row, which raised `KeyError` when that row was missing, which is
precisely the dangling-chunk state the reconciler reports. A gate that crashes cannot
fail a release; it fails the job that was supposed to decide, and a failing job gets
retried and then muted.

Predict before running: after the lineage edge is removed, how many checks fail?

Previous: lesson 8 found the drift. Next: lesson 10 rebuilds after losing everything.
See README section 9 and TEXTBOOK.md section 19.8.

Run it:

    python examples/09_lineage_and_quality.py
"""

from ai_data.catalog import InMemoryCatalog
from ai_data.embedding import DeterministicEmbedder
from ai_data.quality import QualityReport, assess_quality

from _fixtures import record


def print_report(label: str, report: QualityReport) -> None:
    """Print every check, passing or failing.

    Showing the passes matters as much as showing the failure. The point of the
    lesson is localization, and you can only see that one check moved if you can see
    the others holding still.
    """

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

    # A healthy corpus: source and index agree, every chunk has an owner and an ACL
    # that matches its document, and every derivative has a lineage edge.
    report = assess_quality(records, catalog)
    print_report("HEALTHY PIPELINE", report)
    edge = next(iter(catalog.lineage.values()))
    print(
        f"\nLINEAGE: {edge.parent_id} -> {edge.child_id} "
        f"via {edge.transform}@{edge.transform_version}"
    )

    # Break exactly one thing: drop a lineage write, as a partially failed transaction
    # would. The data is still retrievable and still correctly permissioned, which is
    # why only the lineage check should notice.
    catalog.lineage.pop(edge.child_id)
    broken = assess_quality(records, catalog)
    print()
    print_report("AFTER A LOST LINEAGE WRITE", broken)
    print(f"\nRelease gate passes: {broken.ok}")
    print("Takeaway: validate coverage, ACLs, lineage, dimensions, and drift before promotion.")


if __name__ == "__main__":
    main()
