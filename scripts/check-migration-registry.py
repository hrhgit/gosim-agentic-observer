#!/usr/bin/env python3
"""Compare the migration files with private.observer_migrations (read-only).

Lists Observer migration files that are not recorded, recorded digests that differ from
the file, and recorded versions without a file. For every unrecorded file it also shows
what of it is already in the database (tables, columns, indexes, triggers, policies,
functions with an identical body), the sign of a migration applied by hand: such a file
must be verified and recorded (version and SHA-256 digest of the file), never re-run by
scripts/deploy-observer-backend.py --apply, which refuses it anyway.

Exit status 1 when anything is out of line. Uses SUPABASE_PROJECT_REF and
SUPABASE_ACCESS_TOKEN like the other organizer scripts. Changes nothing.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_deploy', ROOT/'scripts/deploy-observer-backend.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)


def check(root=ROOT):
    files = {p.stem: p for p in deploy.observer_migrations(root)}
    exists = deploy.query("select to_regclass('private.observer_migrations') is not null as present")[0]['present']
    recorded = {r['version']: r['digest'] for r in deploy.query('select version,digest from private.observer_migrations')} if exists else {}
    report = {'unrecorded': {}, 'digest_differs': [], 'recorded_without_file': sorted(v for v in recorded if v not in files)}
    for version, path in files.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if version not in recorded:
            report['unrecorded'][path.name] = {'digest': digest, 'already_present': deploy.already_present(path.read_text())}
        elif recorded[version] != digest:
            report['digest_differs'].append(path.name)
    return report


def main():
    report = check()
    print(json.dumps(report, indent=1))
    if report['unrecorded'] or report['digest_differs']: raise SystemExit(1)


if __name__ == '__main__': main()
