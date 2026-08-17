"""Stable identities and content-addressed work reuse.

The invariant this module keeps: an identity is a pure function of the fields that
define it, so the same input produces the same ID on every worker, in every process,
after every restart. That is what makes the whole pipeline idempotent. Nothing here
may depend on wall-clock time, insertion order, or a database sequence.

Two identities are deliberately different in kind. A document ID answers "which
authorized thing is this?" and includes the tenant. A blob ID answers "have we
already done this work?" and includes only the bytes. Confusing them is how one
tenant's cache reuse becomes another tenant's data leak.
"""

from __future__ import annotations

import hashlib
import unicodedata

# A NUL byte cannot appear in the identifiers being joined, so it delimits the parts
# unambiguously: ("ab", "c") and ("a", "bc") hash differently. Concatenating without a
# delimiter would give both the same ID, which is a tenant-isolation bug waiting to
# happen. Keep this value fixed. Changing it changes every ID in every existing index.
_FIELD_SEPARATOR = "\0"


def normalize_text(text: str) -> str:
    """Normalize representation without changing meaningful internal spacing."""

    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n")
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def sha256_hex(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def document_id(tenant_id: str, external_id: str) -> str:
    """Keep equal external IDs in different tenants cryptographically distinct."""

    fingerprint = _FIELD_SEPARATOR.join((tenant_id, external_id))
    return f"doc_{sha256_hex(fingerprint)[:24]}"


def blob_id(content: bytes) -> str:
    """Address raw bytes for parse/embed reuse without merging document identity."""

    return f"blob_{sha256_hex(content)}"


def text_hash(text: str) -> str:
    return sha256_hex(normalize_text(text))


def chunk_id(document_key: str, ordinal: int, text: str) -> str:
    """Identify a chunk by its document, its position, and its text.

    All three matter. Without the document the same sentence in two documents would
    collide; without the ordinal a document that repeats a paragraph would index it
    once; without the text an edited chunk would silently keep its old embedding.
    """

    fingerprint = _FIELD_SEPARATOR.join((document_key, str(ordinal), text_hash(text)))
    return f"chk_{sha256_hex(fingerprint)[:24]}"
