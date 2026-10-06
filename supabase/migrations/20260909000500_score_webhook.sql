-- Notify the score-results edge function when a `results` submission is inserted (pg_net, no dashboard webhook needed).
-- Configure on the hosted project (values are NOT part of the migration):
--   insert into private.config (key, value) values ('score_results_url', 'https://<ref>.supabase.co/functions/v1/score-results'), ('score_results_secret', '<SCORER_WEBHOOK_SECRET>')
--   on conflict (key) do update set value = excluded.value;
create table if not exists private.config (key text primary key, value text not null);
revoke all on private.config from public, anon, authenticated;

create or replace function private.notify_score_results() returns trigger
language plpgsql security definer set search_path = public, private as $$
declare v_url text; v_secret text;
begin
  if new.kind <> 'results' then return new; end if;
  select value into v_url from private.config where key = 'score_results_url';
  select value into v_secret from private.config where key = 'score_results_secret';
  if v_url is null or not exists (select 1 from pg_extension where extname = 'pg_net') then return new; end if;
  begin
    perform net.http_post(
      url := v_url,
      headers := jsonb_build_object('Content-Type', 'application/json', 'x-webhook-secret', coalesce(v_secret, '')),
      body := jsonb_build_object('type', 'INSERT', 'table', 'submissions', 'schema', 'public', 'record', to_jsonb(new)),
      timeout_milliseconds := 5000);
  exception when others then
    raise warning 'score_results webhook failed: %', sqlerrm;
  end;
  return new;
end $$;

drop trigger if exists score_results_webhook on public.submissions;
create trigger score_results_webhook after insert on public.submissions for each row execute function private.notify_score_results();
