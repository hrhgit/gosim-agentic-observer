"""Announcements re-pop only on an explicit "remind everyone" (migration 20261005040000): notify_version starts at 1,
only admins bump it, and earlier text-hash seen records count as seen for version 1."""
import uuid
from pathlib import Path

import psycopg
import pytest

from test_project_database import query
from pg import start

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = '20261005040000_announcement_notify_version.sql'


def register(uri):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    return user


def test_notify_version_and_seen_migration():
    server, uri = start(apply_migrations=False)
    try:
        query(uri, (ROOT / "tests/supabase/auth_stub.sql").read_text())
        migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        for path in migrations:
            if path.name < MIGRATION:
                query(uri, path.read_text())
        ann = query(uri, "insert into public.announcements(title_en,title_zh) values('a','a') returning id")[0][0]
        user, admin = register(uri), register(uri)
        query(uri, "update public.profiles set is_admin=true where id=%s", (admin,))
        query(uri, "insert into private.popup_seen(user_id,key) values(%s,%s),(%s,%s),(%s,'quota:x')",
              (user, f'ann:{ann}:1a2b3c4d', user, f'ann:{ann}:deadbeef', user))
        for path in migrations:
            if path.name >= MIGRATION:
                query(uri, path.read_text())
        seen = sorted(r[0] for r in query(uri, "select key from private.popup_seen where user_id=%s", (user,)))
        assert seen == sorted([f'ann:{ann}:1a2b3c4d', f'ann:{ann}:deadbeef', f'ann:{ann}:v1', 'quota:x'])
        assert query(uri, "select notify_version from public.announcements where id=%s", (ann,))[0][0] == 1
        # Ordinary edits leave it alone; only an admin's renotify bumps it.
        query(uri, "update public.announcements set body_zh='edited' where id=%s", (ann,))
        assert query(uri, "select notify_version from public.announcements where id=%s", (ann,))[0][0] == 1
        with pytest.raises(psycopg.Error, match='forbidden'):
            query(uri, "select public.renotify_announcement(%s)", (ann,), role='authenticated', user=user)
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, "select public.renotify_announcement(%s)", (ann,), role='anon')
        assert query(uri, "select public.renotify_announcement(%s)", (ann,), role='authenticated', user=admin)[0][0] == 2
        # Re-running the migration changes nothing.
        query(uri, (ROOT / "supabase/migrations" / MIGRATION).read_text())
        assert query(uri, "select notify_version from public.announcements where id=%s", (ann,))[0][0] == 2
    finally:
        server.cleanup()
