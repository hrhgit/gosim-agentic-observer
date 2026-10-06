-- observer_reserve_upload caps a team at 5 concurrent pending (unconsumed,
-- unexpired) upload reservations, but each reservation defaulted to a 1-day
-- expiry. A reservation only needs to live long enough for the client to
-- request the slot and immediately upload to it (seconds, not hours); any
-- attempt that never finishes (dropped network, closed tab, a Supabase
-- outage) permanently occupied a slot for up to a full day. Once a team
-- accumulated 5 such abandoned reservations, every further "upload and
-- prepare" click failed instantly with 'upload_limit', surfaced to
-- participants as a generic "operation failed, refresh and retry" with no
-- way to recover before the day was up. Shorten the window so abandoned
-- reservations free themselves quickly, and immediately release everything
-- already stuck from before this change.
alter table private.observer_uploads alter column expires_at set default now() + interval '15 minutes';

update private.observer_uploads set expires_at = now()
  where consumed_at is null and expires_at > now() and created_at < now() - interval '5 minutes';
