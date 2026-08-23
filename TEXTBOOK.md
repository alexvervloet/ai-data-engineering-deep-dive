# Chapter 19: The Corpus Is the Product

*This is the textbook chapter for the AI Data Engineering deep dive, a bonus chapter that slots in after [RAG](../rag-deep-dive/TEXTBOOK.md) and before [Production](../ai-in-production-deep-dive/TEXTBOOK.md). The [README](README.md) is the lab manual; this is the lecture. It covers the work RAG assumes has already happened: getting documents out of the systems that own them, keeping them current as they change, keeping them separated by who is allowed to read them, and being able to prove any of it. Chapter 4 taught retrieval quality. This chapter is about whether the thing being retrieved is the right corpus at all.*

---

## 19.1 The document that was deleted in March

Here is a failure that does not look like a failure.

A company ships an internal assistant over its wiki and its document store. It works. Six months later, someone asks it about the parental leave policy and gets a clear, well-cited answer describing a policy that was replaced in March. The citation is real. The document is real. It was archived five months ago, and nobody has been able to find it in the wiki since, because it is not there. It is only in the index.

Trace it back and the cause is dull. The connector subscribes to change notifications. During a deploy in March the worker was down for forty minutes. The provider retains change history for twenty-four hours, so the events were still there when it came back, except that the worker had persisted its cursor before the index write rather than after, so it resumed past the gap. Two documents were affected. One of them was deleted. Nothing errored. No dashboard moved. Retrieval quality metrics stayed excellent, because the system retrieved the requested document accurately: it was the corpus that was wrong, and no retrieval metric measures the corpus.

Every element of that story is ordinary engineering, and none of it is about machine learning. That is the point of this chapter. A retrieval system is a data pipeline with an embedding step in the middle, and the failures that hurt in production are pipeline failures: missed deletes, replayed events, mixed-up tenants, silent parse failures, cursors that moved when they should not have. Chapter 4 gave you a retriever. This chapter is about everything that decides what is in it.

The one big idea:

> **A retrieval index is a disposable, derived view of authoritative source data.**

Say it back in its two halves, because both do work. *Derived*: the index is not where facts live. It is a projection of somebody else's system of record, and when the two disagree, the index is wrong by definition. *Disposable*: you should be able to delete the entire vector store on a Friday and have it back by Monday, because everything in it can be recomputed from the source snapshot and the change log. If that is not true, the vector database has become a system of record without anyone deciding it should, and it is a bad one: it has no audit trail, no version history, no permissions model of its own, and its contents are lossy summaries of documents you no longer have.

Once you accept the framing, a set of design decisions stops being a matter of taste. Source versions beat arrival order. Access control lists are data that travels with the chunk, not a filter applied at query time. Deletes become durable facts rather than the absence of a row. Every derived artifact remembers what produced it. Backups protect the source snapshot and the cursor, not the vectors.

## 19.2 Getting the data out is the hard part

The pipeline starts at a connector, and connectors are where the reality of other people's systems arrives.

Two jobs live here and they are usually built as if they were separate features. The first is the initial crawl: read everything the source has, once. The second is the incremental feed: keep up with what changes afterward. They are not two features. They are one protocol, and the seam between them is where corpora silently go wrong.

The protocol is four steps: capture the source's high-watermark, read a snapshot at that same logical instant, consume changes strictly after that watermark, and persist the new cursor only after the index write commits. The lab makes each step visible with an in-memory connector whose cursors are plain integers, so you can start the change feed one position too early or too late and watch what happens.

What happens is asymmetric, and the asymmetry is the whole lesson. Start too early and you reprocess documents you already have. That costs money and nothing else, because version-aware writes turn a duplicate into a no-op. Start too late and the documents that changed during your crawl are never seen again by anything, until a customer notices the answer is out of date. Given a choice between paying twice and losing data invisibly, you overlap the window and rely on idempotent writes. Design your pipeline so the cheap failure is the one that happens.

This machinery is not new and it is not from the AI world. Change data capture goes back to database replication, and the modern shape of it (read a consistent snapshot, then follow the write-ahead log from the exact position that snapshot was taken at) is the thing Debezium and every log-based CDC tool implement, on a design lineage that runs through decades of replication research. Nobody in AI infrastructure invented it, and that is good news: it means the failure modes are documented, the vocabulary is stable, and the correct answers are known.

One practical note that costs teams real incidents: treat a provider's cursor as an opaque token. It may be a timestamp, a log sequence number, a page token, or vendor JSON. The moment your code does arithmetic on it, adds a second to it, or compares two of them, you have taken a dependency the provider never offered, and it will hold right up until the provider changes the format.

## 19.3 Versions beat arrival order

The second idea is what makes everything else survivable: **every write decides what to do by comparing versions, not by trusting that it arrived in order.**

Distributed systems deliver at-least-once. Queues redeliver, workers crash between an effect and its acknowledgment, retries fire on requests that actually succeeded, and a network partition delivers your Tuesday events on Wednesday. Exactly-once delivery is mostly unavailable end to end, and the practical substitute is at-least-once delivery plus effects that can be repeated without changing the answer. Each document carries a monotonic source version. An arriving event whose version is not newer than what is stored is reported as stale and does nothing at all.

That single rule buys three properties that would otherwise need coordination. Replay after a crash is a no-op. Out-of-order delivery resolves to the newest version rather than the last-arrived one. And the checkpoint ordering problem from the story in 19.1 becomes recoverable: because you apply the change first and persist the cursor second, a crash between the two replays events that were already applied, and replay is free.

Then there is the case that is easy to get wrong, which the lab gets wrong on purpose so you can watch it. Deletion is a source fact with a version of its own, so the index keeps a tombstone: a record that says "this document was deleted at version 7", rather than simply removing the row. Without it, a late-arriving retry of the version 5 upsert finds nothing in the index, concludes the document is new, and resurrects content the source deleted. The chunks come back. The answer cites them. Nothing errors.

This repository has two entries in its own [LESSONS.md](LESSONS.md) about exactly that comparison, and the second one is instructive about how these bugs behave. The first was straightforward: accepting an equal version made repeat syncs harmless, and also let an equal-version late upsert clear a tombstone. Fixed by requiring strictly newer versions. But there is a mode that is allowed to rewrite a document at its existing version, the backfill, which exists so that a parser or embedding-model migration can re-derive a corpus whose source did not change. That mode had the same hole, discovered later in an audit: a routine migration, run against a snapshot captured around a delete, could republish a deleted document. Same bug, one layer down, hiding behind a legitimate exception to the rule. It is a good illustration of why the honest rule is narrow: only a strictly newer source event lifts a delete, and no convenience mode gets to be an exception.

## 19.4 Parsing is a transform, and it has a version

Between the source and the index sits the least glamorous code in the system, and per hour invested it decides more of your quality than any retrieval parameter.

Real corpora are not clean text. They are PDFs with two-column layouts, scanned documents that need OCR, HTML with navigation chrome and cookie banners, exports whose tables become word salad. Everything downstream inherits whatever this code produces, which is why the RAG chapter's closing line about ingestion (garbage in, confidently cited garbage out) is worth repeating here with a mechanism attached.

The mechanism is that parsed text carries a content hash and a parser version. Both fields exist for the same future moment: the day you change the parser. An HTML cleanup, a PDF library upgrade, an OCR model swap, or a Unicode normalization fix can change the text of every document in the corpus, which changes every chunk, which invalidates every embedding. Without the parser version, you cannot tell which documents were processed by the code that had the bug. Without the content hash, you cannot tell whether re-parsing actually changed anything, so the migration re-embeds everything, including the ninety percent that came out identical.

Two design rules follow, and the lab makes both concrete.

**Fail closed.** The example pipeline refuses PDFs and images unless an OCR adapter is supplied, rather than indexing them as empty text. An empty document is the worst available outcome: it exists, it is never retrieved, and no error is ever raised, so the failure is discovered by a user asking why the assistant does not know about a document they can see with their own eyes. An error at ingest time is a bad afternoon. A silent empty parse is a bad quarter.

**Keep only what a person wrote.** During the audit of this repository, the HTML extractor turned out to keep the text inside `<script>` and `<style>` tags, so minified CSS and JavaScript were being indexed as document content. That is retrieval noise, and it is also something more interesting. Script text is arbitrary text on a page you did not write, and everything the parser keeps eventually lands in a model's context window. That is the ingestion end of [Chapter 7](../prompt-injection-deep-dive/TEXTBOOK.md)'s subject: the parser is the first place where you decide what your model will be asked to read, and it is a much cheaper place to filter than the prompt.

## 19.5 Two questions that identity has to answer separately

Identity in a retrieval pipeline answers two questions that look similar and must never be merged.

The first is "which authorized thing is this?" That is a document ID, and it must include the tenant. Two customers can both have a file called `handbook.md`, and if the ID is derived from the filename alone, one customer's update overwrites the other's document. The lab hashes tenant and external ID together with a separator between them, which sounds fussy until you notice that concatenating without one gives `("acme", "bguide")` and `("acmeb", "guide")` the same identity.

The second is "have we already done this work?" That is a content address: a hash of the bytes. If two tenants store the identical file, parsing it twice and embedding it twice is pure waste, and reusing that work is safe.

The trap is reusing the wrong one. Content-addressed reuse is an optimization on *compute*. It must never become shared *identity*, because ACLs, source URIs, lineage, and version history all belong to the document, not to the bytes. Merge them and one tenant's permissions change silently applies to another tenant's copy. So the rule to carry out of this section: caches may be keyed by content hash, and authorization must stay keyed by document.

There is one more field the cache key needs, which is easy to leave out because leaving it out never raises an error. An embedding cache keyed by content alone will happily serve a vector produced by last quarter's model to a query embedded by this quarter's, and the result is not a crash. It is similarity scores that are meaningless and look fine. The key is the model and its dimensions plus the content hash, and this repository has a commit fixing exactly that omission.

## 19.6 Permissions are data, not a query filter

Multi-tenancy is where a retrieval bug stops being a quality problem and becomes an incident with a lawyer attached.

The rule is that the access control list is copied onto every derivative. The document has an ACL, so every chunk cut from it carries that ACL, and the index stores it beside the vector. Permissions are not metadata to look up later. They are part of the record, because at query time the chunk is what you have.

Then the query order matters, and the correct order is: derive the tenant and the principals from trusted application context, filter to what that caller may read, rank only the authorized candidates, and return provenance with each result. The tempting alternative is to rank first and filter afterward, and it fails twice. It leaks, because the protected content has already left the database and entered your application, possibly your logs, and possibly the model's context. And it silently under-returns, because a top-ten that loses six unauthorized rows becomes a top-four with no explanation to the user.

The word "trusted" in "trusted application context" is load-bearing, and it points at the connection between this dive and the agent chapters. If a tenant ID can arrive in a message, then anything that can write a message can choose a tenant, and in an agentic system a model writes the messages. A tenant selected by model output is not authorization, it is a suggestion.

Which brings up the most useful thing this audit turned up, because it is the kind of mistake that passes review.

The lab's Postgres backend uses row-level security as a second layer: a policy on the chunk table that re-checks tenant and ACL inside the database, so that an application query which forgets its predicates still returns nothing. The policy was written correctly. It was enabled correctly. It was also completely inert, and a probe against the live container proved it: a session set to the wrong tenant, running a query with no filters at all, read every chunk in every tenant.

The reason is a Postgres rule that is documented, reasonable, and very easy to walk past: **a table's owner is exempt from that table's row-level security policies** unless the table is explicitly declared `FORCE ROW LEVEL SECURITY`. Applications routinely connect as the same role that ran the migrations, which is the owner, so the policy protects nobody. The fix in the lab is the one production systems use: searches drop to an unprivileged role that owns nothing and holds `SELECT` on the chunk table and nothing else, so the policy applies and the read path additionally cannot reach the document table or write anything at all. Both halves are now tests, including one that asserts the owner bypass still happens, so the gotcha stays visible instead of becoming folklore.

The general lesson is worth more than the specific flag. A security control that is present, syntactically correct, and enforcing nothing is worse than an absent one, because it terminates the conversation. Nobody audits the layer that is already there. Test that your controls deny something, not just that they exist.

## 19.7 Reconciliation, or assuming you missed one

Everything above assumes events arrive and get applied. Reconciliation assumes the opposite, and it is the difference between a pipeline that degrades gracefully and one that degrades invisibly.

Over a long enough window, some event was missed. A webhook was dropped during a deploy. A cursor was rolled back by a database restore. A connector was down longer than the provider's change retention, which is a specific and common way to lose data permanently: the events expired while you were away, so nothing on either side will ever mention them again. None of these produce an error at the time.

So you compare index state against source truth on a schedule, in both directions. Walking the source finds documents that are missing, stale, or whose ACL has drifted. Walking the index finds documents the source no longer has, and chunks whose document is gone. The lab deliberately drops a delete event, shows reconciliation finding the orphan, applies the tombstone, and then confirms that a late version-1 upsert still cannot bring it back.

Repair is where reconciliation turns dangerous, and it deserves its own warning. Findings are computed against a source snapshot, and a snapshot taken while the source API was half-degraded looks exactly like a source that deleted a great many documents. "Delete everything I did not see" is a rule that reads as correct and empties a tenant the first time a connector has a bad afternoon. Bound the repair: a budget on how much one run may remove, an alert when the budget is hit, and a human in the path above it.

## 19.8 Gates that run before the eval does

[Chapter 5](../evals-deep-dive/TEXTBOOK.md) made quality a number you can rerun, and [Chapter 16](../observability-deep-dive/TEXTBOOK.md) made it a trend you watch. Both measure the system's answers. Neither can see the failures in this chapter, and it is worth being precise about why: a stale corpus scores perfectly against a stale eval set. The retrieval was accurate. The ranking was good. The document was five months out of date, and no answer-quality metric contains the information needed to notice.

So there is an earlier gate, and it checks the corpus rather than the answers: source coverage, reconciliation drift, empty chunks, consistent embedding dimensions, ACL parity between documents and their chunks, lineage coverage for every derivative, duplicate ratios, and active documents that somehow have no chunks. Each check measures one thing on purpose, following the same diagnostic principle as Chapter 4's split between retrieval and generation metrics: when a gate fails, the check that moved should name the stage that broke. The lab's demonstration removes a single lineage edge and shows exactly one check going red while every other stays green.

One design detail is easy to miss and cost this repository a bug. A gate has to survive the corruption it exists to detect. The quality report used to read each chunk's ACL through its document row, which raised `KeyError` when that row was missing, which is precisely the dangling-chunk state the reconciler is built to report. A gate that crashes cannot fail a release; it can only fail the job that was supposed to decide, and a failed job gets retried and then muted.

Lineage is the unglamorous piece that makes all of this debuggable later. Every chunk records which document produced it, through which transform, at which version. When someone asks why the assistant said something strange, lineage is what turns that question into a query instead of an afternoon. It is the same instinct that produced the OpenLineage specification in the wider data engineering world, applied to derived text.

## 19.9 Backups protect the source, not the vectors

Recovery is where the chapter's one big idea pays for itself.

If the index is a derived view, then backing up the vector table is close to worthless. Vectors are the one artifact you can recompute. What you cannot recompute is the source snapshot (the bytes, versions, ACLs, and metadata as they were) and the CDC cursor (your exact position in the source's change history). Back those up, checksum them, and the rebuild is: restore the snapshot, re-parse, re-chunk, re-embed, re-index, then replay every change since the cursor.

That also gives you two numbers to state rather than guess. The recovery point objective is how much source and change history you can afford to lose, which the backup interval determines. The recovery time objective is how long a full rebuild takes at your real corpus size, which you find out by running one, because parsing and embedding a corpus is measured in hours and provider rate limits, not in the seconds a database restore takes. A team that has never rebuilt does not have an RTO; it has an aspiration.

Restore deserves one more piece of respect, since it runs on the worst day of your quarter. It is an untrusted boundary like any other. The bytes have been sitting in storage for months, the code reading them is newer than the code that wrote them, and a contract that has since tightened will reject records that were perfectly valid when they were saved. That is worth distinguishing from corruption in your error handling, because the two have different repairs: corruption sends you to another copy, while incompatibility means the contract moved while the backup sat still, and you need a migration rather than a different tape.

## 19.10 What this buys, and when it is too much

The capstone puts the whole lifecycle against a real Postgres with pgvector: strict manifest, transactional document replacement, tenant and ACL predicates, a reader role that row-level security applies to, tombstones for removed documents, and a quality gate that has to pass. Running it teaches one more thing worth stating plainly, which is that the relational database is doing most of the work. The vectors are one column. The tenant, external ID, source version, content hash, ACL, parser version, and deletion state living beside them are what make the corpus operable, and they are ordinary columns with ordinary indexes.

The vector-specific part still has a tradeoff that catches people, and it is the same shape as every approximate index tradeoff in Chapter 4: an approximate scan stops once it has enough candidates, and for a filtered query those candidates may include almost nothing the caller is allowed to read, so you get too few results rather than a wrong one. pgvector's iterative scans exist for that case, searching further until enough authorized candidates are found. At scale, measure filtered recall rather than assuming it, and consider partitioning by tenant when isolation matters more than index sharing.

Now the honest counterweight, in the spirit of the series. Most of this chapter is overkill for a static corpus. If you are indexing fifty PDFs that never change, for one team, with no access control, then a script that rebuilds the whole index from scratch every night is the correct architecture, and every mechanism above is expensive ceremony. The machinery pays for itself exactly when three things are true: the corpus changes, more than one group of people can read it, and someone will eventually ask why the system said what it said. When those hold, the cost of not building it is not a slower system. It is a system that is confidently wrong in ways nothing measures.

The demotivating truth, and the reason this chapter exists at the end of the bonus track rather than the start of the core one, is that the interesting part of a production retrieval system is almost never the retrieval. It is connectors, versions, permissions, deletes, and proof. That work is unglamorous, it is well understood outside the AI industry, and it is what separates a demo that impresses a room from a system that is still right in March.

---

*Lab manual: [README.md](README.md) · Exercises: [EXERCISES.md](EXERCISES.md) · Slots in after: [RAG](../rag-deep-dive/TEXTBOOK.md), before [Production](../ai-in-production-deep-dive/TEXTBOOK.md) · Pairs with: [Prompt Injection](../prompt-injection-deep-dive/TEXTBOOK.md) and [Observability](../observability-deep-dive/TEXTBOOK.md)*
