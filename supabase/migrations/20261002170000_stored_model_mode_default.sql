-- Saving a model key encrypted on the server becomes the default again. With the
-- page relay, the page must stay open for every evaluation and contestants kept
-- forgetting, so a team that has never explicitly chosen a mode now defaults to
-- "stored" instead of "relay".
--
-- "Not chosen" is already exactly represented: a team only has a row in
-- private.observer_team_model_modes once it saved a key or called
-- observer_set_team_model_mode (either value, including an explicit 'relay'
-- choice). No row means never chosen. This migration therefore only flips the
-- read-path default; it changes no data and does not touch auto-purge.
create or replace function private.observer_team_model_mode(p_team uuid)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select mode from private.observer_team_model_modes where team_id=p_team),'stored')
$$;
revoke all on function private.observer_team_model_mode(uuid) from public,anon,authenticated;

notify pgrst,'reload schema';
