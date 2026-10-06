-- Two organizer switches:
--   mechanics_public      : whether the site advertises the finals anomaly mechanics (tutorial
--                           section, FAQ entries, walkthrough step). Content is hidden, not deleted.
--   registration_deadline : ISO timestamp after which sign-up closes automatically. Existing
--                           accounts keep logging in; only new-account creation stops.

insert into public.site_settings (key, value) values
  ('mechanics_public', 'true'::jsonb),
  ('registration_deadline', 'false'::jsonb)
on conflict (key) do nothing;

drop policy if exists "settings public keys" on public.site_settings;
create policy "settings public keys" on public.site_settings for select to anon, authenticated
  using (key in ('registration_open', 'event', 'mechanics_public', 'registration_deadline') or public.is_admin());

-- The one authoritative answer to "can people still sign up right now?".
create or replace function public.registration_effectively_open()
returns boolean language sql stable security definer set search_path = public as $$
  select coalesce((select value from public.site_settings where key = 'registration_open') <> 'false'::jsonb, true)
     and coalesce(
       (select case
          when value is null or jsonb_typeof(value) <> 'string' then true
          else now() < (value #>> '{}')::timestamptz
        end from public.site_settings where key = 'registration_deadline'), true);
$$;
grant execute on function public.registration_effectively_open() to anon, authenticated;

-- Enforce at the database, not just in the form: after the deadline the sign-up trigger refuses
-- to create the profile, which aborts the whole auth transaction. Logins are untouched.
-- (While closed, admin-API user creation is blocked too: reopen the toggle to add someone.)
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_admins jsonb := coalesce((select value from public.site_settings where key = 'admin_emails'), '[]'::jsonb);
  v_meta jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
begin
  if not public.registration_effectively_open() then
    raise exception 'registration_closed';
  end if;
  insert into public.profiles (id, email, name, github, affiliation, looking_for_team, locale, is_admin,
                               astro_level, ai_level, city, contact, heard_from, long_term, blurb, show_on_wall, role)
  values (
    new.id, new.email,
    coalesce(v_meta->>'name', split_part(coalesce(new.email, ''), '@', 1)),
    coalesce(v_meta->>'github', ''),
    coalesce(v_meta->>'affiliation', ''),
    coalesce(v_meta->>'looking_for_team', '') = 'true',
    coalesce(v_meta->>'locale', 'zh'),
    v_admins ? lower(coalesce(new.email, '')),
    case when coalesce(v_meta->>'astro_level', '') ~ '^[0-3]$' then (v_meta->>'astro_level')::smallint else 0 end,
    case when coalesce(v_meta->>'ai_level', '') ~ '^[0-3]$' then (v_meta->>'ai_level')::smallint else 0 end,
    left(coalesce(v_meta->>'city', ''), 120),
    left(coalesce(v_meta->>'contact', ''), 200),
    left(coalesce(v_meta->>'heard_from', ''), 120),
    coalesce(v_meta->>'long_term', '') = 'true',
    left(coalesce(v_meta->>'blurb', ''), 160),
    coalesce(v_meta->>'show_on_wall', '') = 'true',
    left(coalesce(v_meta->>'role', ''), 120)
  )
  on conflict (id) do nothing;
  return new;
end $$;

notify pgrst, 'reload schema';
