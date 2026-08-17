"""Verify the offline course and report optional Postgres readiness."""

from __future__ import annotations

import importlib.util
import os
import sys


def main() -> int:
    errors: list[str] = []
    print("AI Data Engineering setup")
    print(f"  Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 11):
        errors.append("Python 3.11 or newer is required")

    try:
        import ai_data

        print(f"  package: {ai_data.__name__} import OK")
    except ImportError as exc:
        errors.append(f"package import failed ({exc}); run: pip install -r requirements.txt")

    psycopg_available = importlib.util.find_spec("psycopg") is not None
    database_configured = bool(
        os.environ.get("AI_DATA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    print(f"  Postgres driver: {'installed' if psycopg_available else 'optional, not installed'}")
    print(f"  database URL: {'configured' if database_configured else 'optional, not configured'}")
    if database_configured and not psycopg_available:
        errors.append(
            "a database URL is set but Psycopg is missing; "
            "run: pip install -r requirements-postgres.txt"
        )

    if errors:
        print("\nFix these before continuing:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("\nOffline lessons are ready. Postgres is required only for the live capstone path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
