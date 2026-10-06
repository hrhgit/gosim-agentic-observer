-- The project has no custom SMTP, so the built-in mailer is capped at two messages an hour project-wide and a
-- participant who forgets their password never receives the reset mail. The `recovery-link` Edge Function mints
-- the link and hands it to an organiser instead. Handing out a sign-in link is an account-level action, so it
-- belongs in the audit log like the other admin actions.
create or replace function public.audit_admin_action(p_action text, p_detail jsonb default '{}'::jsonb)
returns void language sql security definer set search_path = public as $$
  select private.audit(p_action, coalesce(p_detail, '{}'::jsonb));
$$;
revoke execute on function public.audit_admin_action(text, jsonb) from public, anon, authenticated;
grant execute on function public.audit_admin_action(text, jsonb) to service_role;
