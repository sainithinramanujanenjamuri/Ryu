#!/usr/bin/env python3
"""
scripts/apply_migrations.py

Applies all SQL migration scripts in deploy/migrations/ to the PostgreSQL database.
"""

from __future__ import annotations

import sys
from pathlib import Path
import psycopg2

def find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent, here]:
        if (candidate / "deploy" / "migrations").is_dir():
            return candidate
    return Path.cwd()

REPO_ROOT = find_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ryu.pulse_bus.config import PostgresConfig


def apply_migrations() -> None:
    repo_root = find_repo_root()
    migrations_dir = repo_root / "deploy" / "migrations"
    if not migrations_dir.is_dir():
        print(f"Error: migrations directory {migrations_dir} not found.")
        sys.exit(1)

    cfg = PostgresConfig.from_env()
    print("Connecting to PostgreSQL:")
    print(f"  Host: {cfg.host}:{cfg.port}")
    print(f"  Database: {cfg.db}")
    print(f"  User: {cfg.user}")

    conn = psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.db,
        user=cfg.user,
        password=cfg.password,
    )
    conn.autocommit = True
    cur = conn.cursor()

    sql_files = sorted(migrations_dir.glob("*.sql"))
    print(f"Found {len(sql_files)} migration files.")

    for sql_file in sql_files:
        print(f"  Applying {sql_file.name}...", end=" ", flush=True)
        content = sql_file.read_text(encoding="utf-8")
        cur.execute(content)
        print("[DONE]")

    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
    )
    tables = [row[0] for row in cur.fetchall()]
    print("\nPublic tables now in database:")
    for t in tables:
        print(f"  - {t}")

    conn.close()
    print("\nMigrations applied successfully!")


if __name__ == "__main__":
    apply_migrations()
