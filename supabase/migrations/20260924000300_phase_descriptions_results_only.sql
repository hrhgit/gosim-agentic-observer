-- The phase descriptions still said agents run on the platform; every phase now takes decisions.csv only.
update public.phases set
  description_en = 'October 5–7. Run your agent locally on competition scenarios A and B — their weather is published when the competition opens — and submit each decisions.csv; the score is the mean over both. These scenarios enable the full anomaly mechanics: hidden tile tags, an instrument fault, score feedback and the report channel — rehearse them on the kit''s finals-preview scenario.',
  description_zh = '10 月 5–7 日。在本地对比赛场景 A、B 运行智能体（天气在开赛时公开），分别提交 decisions.csv，得分为两个场景的平均值。正式赛场景启用完整异常机制：隐藏天区标签、仪器故障、实现分反馈与上报通道——可用入门包里的 finals-preview 场景演练。'
where slug = 'online';

update public.phases set
  description_en = 'Open now. Run your agent locally on the public development scenarios and submit its decisions.csv. Unlimited practice; the practice board is informational.',
  description_zh = '现已开放。在本地对公开开发场景运行智能体，提交 decisions.csv。练习不限次数，练习榜仅供参考。'
where slug = 'practice';
