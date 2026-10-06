-- Run this only in the dedicated Cosmos26 Supabase project, never Factory26.
create extension if not exists pgcrypto;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null default '', email text, github_id text, role text, discord text,
  looking_for_team boolean not null default true,
  team_id uuid, created_at timestamptz not null default now()
);
create table if not exists public.teams (
  id uuid primary key default gen_random_uuid(), name text not null,
  leader_id uuid not null references public.profiles(id), project_idea text,
  github_repo text, max_size integer check (max_size is null or max_size > 0),
  created_at timestamptz not null default now()
);
alter table public.profiles drop constraint if exists profiles_team_id_fkey;
alter table public.profiles add constraint profiles_team_id_fkey foreign key (team_id) references public.teams(id) on delete set null;

create or replace function public.handle_cosmos26_user() returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, name, github_id, role, discord, looking_for_team)
  values (new.id, new.email, coalesce(new.raw_user_meta_data->>'name',''), new.raw_user_meta_data->>'github_id', new.raw_user_meta_data->>'role', new.raw_user_meta_data->>'discord', coalesce((new.raw_user_meta_data->>'looking_for_team')::boolean,true))
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_cosmos26_auth_user_created on auth.users;
create trigger on_cosmos26_auth_user_created after insert on auth.users for each row execute function public.handle_cosmos26_user();

alter table public.profiles enable row level security;
alter table public.teams enable row level security;
create policy "profiles readable by participants" on public.profiles for select to authenticated using (true);
create policy "participants update own profile" on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());
create policy "participants insert own profile" on public.profiles for insert to authenticated with check (id = auth.uid());
create policy "teams readable by participants" on public.teams for select to authenticated using (true);
create policy "participants create own team" on public.teams for insert to authenticated with check (leader_id = auth.uid());
create policy "leaders update own team" on public.teams for update to authenticated using (leader_id = auth.uid()) with check (leader_id = auth.uid());
