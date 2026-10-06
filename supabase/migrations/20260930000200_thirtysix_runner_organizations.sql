-- Up to thirty-six runner organizations may share the GitHub Actions budget.
-- Runner organizations 13-36 are being prepared one by one; only the two
-- organization-name check constraints are relaxed here. Scheduling is
-- unchanged: placement still picks only enabled rows of
-- private.observer_installations, and a new organization stays enabled=false
-- until its app installation and control repository are verified with
-- scripts/configure-observer-runners.py.
alter table private.observer_installations drop constraint observer_installations_organization_check;
alter table private.observer_installations add constraint observer_installations_organization_check
  check (organization ~ '^AGENTIC-OBSERVER26-runner-([1-9]|[12][0-9]|3[0-6])$');
alter table private.observer_materializations drop constraint observer_materializations_archive_ref_check;
alter table private.observer_materializations add constraint observer_materializations_archive_ref_check
  check (archive_ref ~ '^github:AGENTIC-OBSERVER26-runner-([1-9]|[12][0-9]|3[0-6])/participant-[0-9a-f]{32}@[0-9a-f]{40}$');
