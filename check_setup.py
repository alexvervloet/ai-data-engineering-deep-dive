"""
check_setup.py: confirm the course runs before you start it.

Run this first. It answers one question, "will the lessons work on this machine?",
and it is deliberately quick to satisfy: the ten lessons and the default capstone path
need Python 3.11 or newer and nothing else. No API key, no network, no service, no
third-party runtime dependency.

The Postgres pieces are reported rather than required, because they are optional
until the live capstone. There is one combination this script treats as an error
though, and it is the interesting case: a database URL configured with no driver
installed. That means somebody set up the environment for the live path and would
otherwise get an import failure partway through a sync, which is a confusing place
to learn about a missing package.

Run it:

    python check_setup.py

Exit status is 0 when the offline course is ready, 1 otherwise, so it also works as
a first CI step.
"""

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
