"""
Lesson 1: validate connector payloads before they become pipeline state.

Every document in your index arrived from a system you do not control, through code
somebody else wrote, describing a schema somebody else may have changed on Tuesday.
This is the boundary where that stops being someone else's problem.

The thing to understand first is that a type hint is not enforcement. Neither is a
schema in a design document, nor a TypedDict, nor a comment saying what the field
means. None of them run. A contract is code that inspects the bytes that actually
arrived and refuses the ones that do not comply, at the one moment when refusing is
still cheap. After this point a bad record is not a rejected payload; it is a wrong
document being cited confidently to a user six months from now.

Three rejections are worth predicting before you run it:

  - An **unknown field** is an error, not something to ignore. The sending team
    believes a field they added is being honored. Silently dropping it means the
    evidence of the misunderstanding is a corpus that has quietly been wrong.
  - An **empty ACL** denies rather than defaulting to public. This is a
    one-character difference between a document nobody can read and a document
    everybody can, and only one of those mistakes is recoverable.
  - A **model-supplied tenant** never reaches authorization. If a tenant can arrive
    in a payload, then anything that can write a payload chooses the tenant, and in
    an agentic system a model writes payloads. Authorization comes from trusted
    session context, always.

Predict before running: is `tenant_from_model` ignored, or does it fail the whole
record? Then check which of the two the pipeline can safely do.

Next: lesson 2 shows where the records come from in the first place.
See README section 1, and TEXTBOOK.md section 19.4 for why parsing has the same
shape as this validation step.

Run it:

    python examples/01_data_contracts.py
"""

from __future__ import annotations

from ai_data.contracts import ContractViolation, source_from_mapping


def main() -> None:
    # A well-formed v2 payload. Note what the contract insists on beyond types: a UTC
    # offset on the timestamp (local time is ambiguous, and a backfill sorts on this),
    # a supported MIME type, a non-empty reader list, and a version the source owns.
    payload: dict[str, object] = {
        "contract_version": "2",
        "tenant_id": "acme",
        "external_id": "deploy-guide",
        "version": 1,
        "updated_at": "2026-08-17T09:00:00+00:00",
        "source_uri": "file:///acme/deploy-guide.md",
        "mime_type": "text/markdown",
        "content": "Deployments require two reviewers.",
        "readers": ["group:engineering"],
        "metadata": {"owner": "platform"},
    }
    accepted = source_from_mapping(payload)
    print("VALID PAYLOAD")
    print(f"  {accepted.tenant_id}/{accepted.external_id} v{accepted.version}")

    # The same payload plus one field the pipeline does not know about. A permissive
    # parser would drop it and carry on, which is the failure that takes months to
    # surface: the sender thinks the field is doing something.
    unsafe = {**payload, "tenant_from_model": "competitor"}
    print("\nUNKNOWN FIELD")
    try:
        source_from_mapping(unsafe)
    except ContractViolation as exc:
        print(f"  rejected: {exc}")

    # An empty reader list is not "no restrictions", it is "nobody". Deny by default
    # is the only safe reading, because the alternative fails open.
    no_acl = {**payload, "readers": []}
    print("\nDENY-BY-DEFAULT ACL")
    try:
        source_from_mapping(no_acl)
    except ContractViolation as exc:
        print(f"  rejected: {exc}")

    print("\nTakeaway: a connector schema is enforced at runtime, not trusted as documentation.")


if __name__ == "__main__":
    main()
