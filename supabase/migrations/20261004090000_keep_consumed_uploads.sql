-- Data-preservation: never auto-delete an upload that was actually used (source zips of
-- prepared revisions, CSVs of scored runs). Only expired, never-consumed upload slots are
-- cleaned. Owner decision 2026-10-04 (no automatic deletion of contestant data during the
-- hackathon). Already applied to production.
CREATE OR REPLACE FUNCTION public.observer_expired_uploads(p_limit integer DEFAULT 50)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
 select coalesce(jsonb_agg(jsonb_build_object('id',u.id,'path',u.path)),'[]') from (
   select u.id,u.path from private.observer_uploads u
   where u.cleaned_at is null and u.expires_at<now()
     and u.consumed_at is null
   order by u.expires_at limit greatest(1,least(coalesce(p_limit,50),100))
 ) u
$function$
;
