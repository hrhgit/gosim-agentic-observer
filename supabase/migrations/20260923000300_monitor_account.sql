-- A site health check logs in every four hours through the real login form with its own account.
-- The account must be able to log in (the site signs out anyone marked banned), yet must not show up in
-- any public count or on the wall. site_settings.excluded_accounts lists such accounts by id; the key is
-- not in the public-read list, so only admins can see it.
insert into public.site_settings (key, value)
select 'excluded_accounts', jsonb_build_array(id::text) from public.profiles where email = 'site-health-check@create.gosim.org'
on conflict (key) do nothing;

create or replace function public.participants_stats()
 returns jsonb language sql stable security definer set search_path to 'public' as $$
  with hidden as (
    select jsonb_array_elements_text(coalesce((select value from public.site_settings where key = 'excluded_accounts'), '[]'::jsonb))::uuid as id
  )
  select jsonb_build_object(
    'total', (select count(*) from public.profiles where not is_banned and id not in (select id from hidden)),
    'on_wall', (select count(*) from public.profiles where show_on_wall and not is_banned and id not in (select id from hidden)),
    'looking', (select count(*) from public.profiles where show_on_wall and not is_banned and looking_for_team and team_id is null and id not in (select id from hidden)),
    'teams', (select count(*) from public.teams));
$$;

-- The monitoring account is an ordinary, unbanned participant from here on.
update public.profiles set is_banned = false, show_on_wall = false, looking_for_team = false
 where email = 'site-health-check@create.gosim.org';

notify pgrst, 'reload schema';
