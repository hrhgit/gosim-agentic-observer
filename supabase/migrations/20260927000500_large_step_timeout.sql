-- A randomized formal scenario's first observation is ~5.7 MB. Storing it can
-- exceed the hosted API's inherited 8-second SQL timeout, which failed runs at
-- step 1 (engine_job_failed / agent_error). Match the other bounded transfers.
alter function public.observer_publish_step(uuid,text,integer,jsonb) set statement_timeout='60s';
notify pgrst,'reload schema';
