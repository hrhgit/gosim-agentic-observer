-- Full formal public catalogs contain ~400,000 targets. Even compressed, the
-- JSON parameter/response can exceed the hosted API's inherited 8-second SQL
-- timeout. PostgREST hoists function settings before executing the statement.
-- Extend only the two bounded catalog transfers, never the role/database or
-- participant decision clock. Existing capabilities and size limits still apply.
alter function public.observer_publish_initial(uuid,text,jsonb) set statement_timeout='60s';
alter function public.observer_poll(uuid,text,text,boolean) set statement_timeout='60s';
notify pgrst,'reload schema';
