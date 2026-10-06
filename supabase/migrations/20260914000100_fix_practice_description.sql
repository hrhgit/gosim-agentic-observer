-- Fix practice phase description: remove "unlimited" claim, state the actual daily limit
update public.phases
set description_en = 'Open now. Score decisions.csv files or run your agent on the public development scenarios. 50 submissions per team per day; the practice board is informational.',
    description_zh = '现已开放。可对公开开发场景提交 decisions.csv 或直接运行智能体。每队每天 50 次，练习榜仅供参考。'
where slug = 'practice';
