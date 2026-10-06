-- Deleting an auth user (GoTrue admin API) must not fail on foreign keys:
--   * a deleted leader dissolves the team (members' team_id becomes null via profiles.team_id on delete set null)
--   * submissions stay with the team; the submitting user becomes null
alter table public.teams drop constraint if exists teams_leader_id_fkey;
alter table public.teams add constraint teams_leader_id_fkey foreign key (leader_id) references public.profiles(id) on delete cascade;
alter table public.submissions alter column user_id drop not null;
alter table public.submissions drop constraint if exists submissions_user_id_fkey;
alter table public.submissions add constraint submissions_user_id_fkey foreign key (user_id) references public.profiles(id) on delete set null;
