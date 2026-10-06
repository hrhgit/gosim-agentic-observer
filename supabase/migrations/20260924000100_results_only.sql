-- The organisers decided (2026-09-24): every phase takes a decisions.csv results file only.
-- Agent packages are no longer accepted anywhere. Submissions already made — agent runs included —
-- keep their scores and stay on the boards; anything already queued is still evaluated.
update public.phases set allow_agents = false, allow_results = true;
