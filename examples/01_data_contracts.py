"""Lesson 1: validate connector payloads before they become pipeline state."""

from __future__ import annotations

from ai_data.contracts import ContractViolation, source_from_mapping


def main() -> None:
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

    unsafe = {**payload, "tenant_from_model": "competitor"}
    print("\nUNKNOWN FIELD")
    try:
        source_from_mapping(unsafe)
    except ContractViolation as exc:
        print(f"  rejected: {exc}")

    no_acl = {**payload, "readers": []}
    print("\nDENY-BY-DEFAULT ACL")
    try:
        source_from_mapping(no_acl)
    except ContractViolation as exc:
        print(f"  rejected: {exc}")

    print("\nTakeaway: a connector schema is enforced at runtime, not trusted as documentation.")


if __name__ == "__main__":
    main()
