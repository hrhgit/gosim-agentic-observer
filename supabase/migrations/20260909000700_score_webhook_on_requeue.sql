-- Also notify the scoring function when an existing submission is put back into the queue (admin rescore).
drop trigger if exists score_results_webhook on public.submissions;
create trigger score_results_webhook after insert or update of status on public.submissions
for each row when (new.status = 'queued') execute function private.notify_score_results();
