"""Operational commands: python -m app.cli <command> [args]."""
from __future__ import annotations

import getpass
import secrets
import sys

from .db import init_db, session_scope
from .models import User
from .security import hash_password
from .services import jobs, seed


def create_admin(email: str, password: str | None = None) -> None:
    init_db()
    with session_scope() as db:
        u = db.query(User).filter(User.email == email.lower()).first()
        pw = password or secrets.token_urlsafe(12)
        if u is None:
            u = User(email=email.lower(), name="Organizer", password_hash=hash_password(pw), is_admin=True, is_verified=True)
            db.add(u)
            print(f"created admin {email} with password: {pw}")
        else:
            u.is_admin = True
            if password:
                u.password_hash = hash_password(pw)
            print(f"{email} is now an admin" + (" (password updated)" if password else ""))


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("commands: create-admin EMAIL [PASSWORD] | seed | worker | run-pending")
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "create-admin":
        create_admin(args[0], args[1] if len(args) > 1 else None)
    elif cmd == "seed":
        init_db()
        with session_scope() as db:
            seed.ensure_bootstrap(db)
        print("seeded")
    elif cmd == "worker":
        init_db()
        jobs.start_workers()
        print("worker running; Ctrl-C to stop")
        try:
            import time
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            jobs.stop_workers()
    elif cmd == "run-pending":
        init_db()
        print(f"processed {jobs.run_pending()} jobs")
    else:
        print(f"unknown command {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
