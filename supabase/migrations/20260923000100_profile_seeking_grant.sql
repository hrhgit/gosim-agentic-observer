-- Participants could not save their profile at all: "permission denied for table profiles".
-- Users may only update the profile columns granted to them one by one (core.sql, participants.sql).
-- 20260920000300 added seeking / seeking_count and the profile and teammates pages started sending
-- both on every save, but the two columns were never granted, so every save was refused outright.
-- Both columns already carry check constraints (seeking in ('', 'astro', 'ai'); count 0–9).
grant update (seeking, seeking_count) on public.profiles to authenticated;

notify pgrst, 'reload schema';
