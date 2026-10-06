"""Embedded Postgres helper shared by the Supabase tests."""
from __future__ import annotations

import pathlib
import tempfile

import pgserver
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))


def start(apply_migrations: bool = True):
    d = tempfile.mkdtemp(prefix="sac-pg-")
    srv = pgserver.get_server(d)
    uri = srv.get_uri()
    if apply_migrations:
        with psycopg.connect(uri, autocommit=True) as conn:
            conn.execute((ROOT / "tests" / "supabase" / "auth_stub.sql").read_text())
            for m in MIGRATIONS:
                conn.execute(m.read_text())
    return srv, uri
