-- Two things the database hid that the site needs, and one it showed that it must not.

-- 1. Participants waiting on an evaluation were always told "no evaluator is reporting", even while one
--    was running: the page reads site_settings.worker_heartbeat, and only admins could read that key.
--    The sponsor-credits panel reads credits_note and was likewise always blank for participants.
--    Neither holds anything private (a worker id, a timestamp, queue counts; an explanatory note).
--    admin_emails stays admin-only.
drop policy if exists "settings public keys" on public.site_settings;
create policy "settings public keys" on public.site_settings for select to anon, authenticated
  using (key in ('registration_open', 'event', 'mechanics_public', 'registration_deadline', 'worker_heartbeat', 'credits_note')
         or public.is_admin());

-- 2. Any logged-in user could read every team's invite code straight from the teams table, and with it
--    join any unlocked team uninvited. Members get their own code from me() (security definer), and the
--    admin console reads codes through admin_teams(), so the column itself can be withheld.
revoke select on public.teams from anon, authenticated;
grant select (id, name, slug, leader_id, project_idea, github_repo, max_size, is_locked, is_hidden, created_at)
  on public.teams to anon, authenticated;

notify pgrst, 'reload schema';
