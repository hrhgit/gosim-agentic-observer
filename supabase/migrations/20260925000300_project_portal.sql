-- Participant portal additions. Staging is private and temporary; existing
-- submissions buckets, boards, phases and registration are untouched.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('observer-staging','observer-staging',false,52428800,array['application/zip','text/csv'])
on conflict(id) do nothing;

create table private.observer_uploads (
  id uuid primary key,
  team_id uuid not null references public.teams(id),
  user_id uuid not null references public.profiles(id),
  purpose text not null check(purpose in ('source','csv')),
  path text not null unique,
  expires_at timestamptz not null default now()+interval '1 day',
  consumed_at timestamptz,
  revision_id uuid references public.observer_revisions(id),
  created_at timestamptz not null default now()
);
revoke all on private.observer_uploads from public,anon,authenticated;

create table public.observer_evidence (
  revision_id uuid primary key references public.observer_revisions(id),
  notes text not null default '' check(length(notes)<=8000),
  code_url text not null default '' check(code_url='' or code_url ~ '^https://[^[:space:]]+$'),
  updated_at timestamptz not null default now()
);
alter table public.observer_evidence enable row level security;
create policy observer_evidence_read on public.observer_evidence for select to authenticated
  using(exists(select 1 from public.observer_revisions r where r.id=revision_id));
revoke all on public.observer_evidence from public,anon,authenticated;
grant select on public.observer_evidence to authenticated;
grant all on public.observer_evidence to service_role;

create function public.observer_save_evidence(p_revision uuid,p_notes text,p_code_url text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform private.assert_not_banned();
  if not exists(select 1 from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
    where r.id=p_revision and public.observer_team(p.team_id)) then raise exception 'revision_not_found'; end if;
  insert into public.observer_evidence(revision_id,notes,code_url) values(p_revision,p_notes,p_code_url)
    on conflict(revision_id) do update set notes=excluded.notes,code_url=excluded.code_url,updated_at=now();
end $$;

create function public.observer_list_providers()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('id',id,'name',name,'base_url',base_url,'models',models,
    'enabled',enabled,'daily_token_limit',daily_token_limit,'shared',team_id is null) order by created_at),'[]')
  from private.observer_providers
  where exists(select 1 from public.profiles where id=auth.uid() and not is_banned and team_id is not null)
    and (team_id is null or public.observer_team(team_id))
$$;

create function public.observer_save_provider(p_user uuid,p_id uuid,p_name text,p_base text,p_encrypted_key text,
  p_models text[],p_limit bigint,p_http boolean)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid;v_owner uuid;
begin
  select team_id into v_team from public.profiles where id=p_user and not is_banned;
  if v_team is null then raise exception 'team_required'; end if;
  perform 1 from public.teams where id=v_team for update;
  select team_id into v_owner from private.observer_providers where id=p_id for update;
  if found and v_owner is distinct from v_team then raise exception 'provider_not_found'; end if;
  if not found and (select count(*) from private.observer_providers where team_id=v_team)>=5 then
    raise exception 'provider_limit'; end if;
  if p_name is null or length(trim(p_name)) not between 1 and 80 or p_encrypted_key is null or length(p_encrypted_key)<20
    or p_base is null or length(p_base)>1000 or p_base !~ '^https?://' or p_http is distinct from (p_base like 'http://%')
    or p_models is null or cardinality(p_models) not between 1 and 20
    or exists(select 1 from unnest(p_models) m where m is null or length(m) not between 1 and 160)
    or p_limit is null or p_limit not between 0 and 10000000 then raise exception 'invalid_provider'; end if;
  insert into private.observer_providers(id,team_id,name,base_url,encrypted_key,models,enabled,daily_token_limit,allow_http)
    values(p_id,v_team,trim(p_name),p_base,p_encrypted_key,p_models,true,p_limit,p_http)
  on conflict(id) do update set name=excluded.name,base_url=excluded.base_url,encrypted_key=excluded.encrypted_key,
    models=excluded.models,enabled=true,daily_token_limit=excluded.daily_token_limit,allow_http=excluded.allow_http;
end $$;

create function public.observer_disable_provider(p_id uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform private.assert_not_banned();
  update private.observer_providers set enabled=false where id=p_id and public.observer_team(team_id);
  if not found then raise exception 'provider_not_found'; end if;
end $$;

create function public.observer_reserve_upload(p_user uuid,p_id uuid,p_purpose text)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid;v_path text;
begin
  select team_id into v_team from public.profiles where id=p_user and not is_banned;
  if v_team is null then raise exception 'team_required'; end if;
  if p_purpose is null or p_purpose not in ('source','csv') then raise exception 'invalid_upload'; end if;
  perform 1 from public.teams where id=v_team for update;
  if (select count(*) from private.observer_uploads where team_id=v_team and expires_at>now() and consumed_at is null)>=5 then
    raise exception 'upload_limit'; end if;
  v_path:=v_team::text||'/'||p_id::text||case when p_purpose='source' then '/source.zip' else '/decisions.csv' end;
  insert into private.observer_uploads(id,team_id,user_id,purpose,path) values(p_id,v_team,p_user,p_purpose,v_path);
  return v_path;
end $$;

create function public.observer_upload_access(p_user uuid,p_id uuid,p_purpose text)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select u.path from private.observer_uploads u join public.profiles p on p.team_id=u.team_id
  where u.id=p_id and p.id=p_user and not p.is_banned and u.purpose=p_purpose
    and u.expires_at>now()
$$;

create function public.observer_submit_zip(p_upload uuid,p_title text)
returns uuid language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_uploads; v_revision uuid;
begin
  perform private.assert_not_banned();
  select * into v from private.observer_uploads where id=p_upload and public.observer_team(team_id) for update;
  if not found or v.purpose<>'source' or v.expires_at<=now() then raise exception 'upload_not_found'; end if;
  if v.revision_id is not null then return v.revision_id; end if;
  v_revision:=public.observer_create_project(p_title,'zip',v.path);
  update private.observer_uploads set consumed_at=now(),revision_id=v_revision where id=p_upload;
  return v_revision;
end $$;

revoke all on function public.observer_save_evidence(uuid,text,text),public.observer_list_providers(),
  public.observer_disable_provider(uuid),public.observer_submit_zip(uuid,text) from public,anon;
grant execute on function public.observer_save_evidence(uuid,text,text),public.observer_list_providers(),
  public.observer_disable_provider(uuid),public.observer_submit_zip(uuid,text) to authenticated;
revoke all on function public.observer_save_provider(uuid,uuid,text,text,text,text[],bigint,boolean),
  public.observer_reserve_upload(uuid,uuid,text),public.observer_upload_access(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.observer_save_provider(uuid,uuid,text,text,text,text[],bigint,boolean),
  public.observer_reserve_upload(uuid,uuid,text),public.observer_upload_access(uuid,uuid,text) to service_role;
