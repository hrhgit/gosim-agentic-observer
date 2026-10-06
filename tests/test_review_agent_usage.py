"""Pure parts of scripts/review-agent-usage.py: source selection, refs, prompts and verdicts."""
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('review_agent_usage', Path(__file__).resolve().parents[1] / 'scripts/review-agent-usage.py')
review = importlib.util.module_from_spec(spec)
sys.modules['review_agent_usage'] = review
spec.loader.exec_module(review)


def verdict(*agent_stages, qualified=None, **extra):
    stages = [{'stage': key, 'uses_agent': key in agent_stages, 'evidence_files': ['agent/llm.py'] if key in agent_stages else [],
               'reason': 'r'} for key in review.STAGE_KEYS]
    return {'qualified': len(agent_stages) >= 2 if qualified is None else qualified, 'stages': stages, 'summary': 's', **extra}


def test_skips_vendored_lockfiles_binaries_and_data():
    assert review.skip_reason('node_modules/x/index.js', 10) == 'vendored'
    assert review.skip_reason('agent/.venv/lib/site.py', 10) == 'vendored'
    assert review.skip_reason('pkg.egg-info/PKG-INFO', 10) == 'vendored'
    assert review.skip_reason('package-lock.json', 10) == 'lockfile'
    assert review.skip_reason('model/weights.PT'.lower(), 10) == 'binary'
    assert review.skip_reason('scenarios/a/weather.csv', 10) == 'data'
    assert review.skip_reason('web/app.min.js', 10) == 'minified'
    assert review.skip_reason('agent/huge.py', 3_000_000) == 'too_large'
    assert review.skip_reason('agent/main.py', 10) is None
    assert review.skip_reason('Dockerfile', 10) is None


def test_selection_orders_own_code_first_and_respects_both_caps():
    files = {
        'challenge/scorer.py': b'x' * 50,
        'agent/main.py': b'import openai\n' + b'y' * 30,
        'README.md': b'# Team',
        'requirements.txt': b'openai\n',
        'agent/prompts/system.md': b'You are a planner',
        'agent/big.py': b'z' * 500,
        'logo.png': b'\x89PNG',
        'notes.txt': b'\xff\xfe\x00bad',
    }
    included, omitted = review.select_files(files, max_chars=200, max_file_chars=100)
    paths = [path for path, _, _ in included]
    assert paths[:2] == ['README.md', 'requirements.txt']
    assert set(paths[2:4]) == {'agent/big.py', 'agent/main.py'}
    assert paths.index('agent/prompts/system.md') > paths.index('agent/main.py')
    big = next(item for item in included if item[0] == 'agent/big.py')
    assert big[2] is True and len(big[1]) == 100
    assert sum(len(text) for _, text, _ in included) <= 200
    reasons = dict(omitted)
    assert reasons['logo.png'] == 'binary' and reasons['notes.txt'] == 'binary'
    assert reasons['challenge/scorer.py'] == 'budget'  # the starter-kit environment comes last


def test_extract_tarball_strips_the_top_directory_and_ignores_unsafe_paths():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in (('org-repo-abc/agent/main.py', b'print(1)'), ('org-repo-abc/../evil.py', b'x')):
            info = tarfile.TarInfo(name); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        directory = tarfile.TarInfo('org-repo-abc/emptydir'); directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
    files = review.extract_tarball(buffer.getvalue())
    assert files['agent/main.py'] == b'print(1)'
    assert 'evil.py' not in files and '../evil.py' not in files


def test_source_ref_prefers_the_teams_own_snapshot():
    sha, prepared = 'a' * 40, 'b' * 40
    row = {'repository': 'AGENTIC-OBSERVER26-runner-3/participant-' + '0' * 32, 'source_commit': sha,
           'archive_ref': f'github:AGENTIC-OBSERVER26-runner-3/participant-{"0" * 32}@{prepared}'}
    assert review.source_ref(row) == (row['repository'], sha)
    assert review.source_ref(row, prepared=True) == (row['repository'], prepared)
    assert review.source_ref({**row, 'source_commit': None}) == (row['repository'], prepared)
    assert review.source_ref({'repository': 'bad repo', 'source_commit': sha, 'archive_ref': 'nope'}) is None
    assert review.parse_archive_ref('github:o/r@' + 'c' * 39) is None


def test_prompt_lists_omissions_and_marks_truncation():
    prompt = review.build_prompt({'team_name': 'Nova', 'revision_id': 'rev', 'version_source': 'chosen', 'ref': 'o/r@sha'},
                                 [('agent/main.py', 'code', True)], [('logo.png', 'binary')])
    assert 'Team: Nova' in prompt and 'logo.png\tbinary' in prompt
    assert '<file path="agent/main.py" truncated="true">\ncode\n</file>' in prompt
    assert all(key in review.SYSTEM_PROMPT for key in review.STAGE_KEYS)
    assert review.RULE_ZH in review.SYSTEM_PROMPT


def test_verdict_is_validated_and_qualification_recomputed():
    ok = review.normalize_verdict(verdict('action_decision', 'plan_adaptation'))
    assert ok['qualified'] is True and ok['agent_stage_count'] == 2 and 'model_qualified' not in ok
    assert [s['stage'] for s in ok['stages']] == list(review.STAGE_KEYS)
    one = review.normalize_verdict(verdict('tool_calling', qualified=True))
    assert one['qualified'] is False and one['model_qualified'] is True
    shuffled = verdict('data_parsing', 'task_planning'); shuffled['stages'].reverse()
    assert [s['stage'] for s in review.normalize_verdict(shuffled)['stages']] == list(review.STAGE_KEYS)
    for bad in ({}, {'stages': 'x'}, {'stages': verdict()['stages'][:5]},
                {'stages': verdict()['stages'] + [verdict()['stages'][0]]},
                {'stages': [{**verdict()['stages'][0], 'stage': 'vibes'}] + verdict()['stages'][1:]}):
        with pytest.raises(review.VerdictError):
            review.normalize_verdict(bad)


def test_schema_matches_the_stage_keys_and_csv_rows_flatten_the_verdict():
    schema = review.VERDICT_SCHEMA
    assert schema['properties']['stages']['items']['properties']['stage']['enum'] == list(review.STAGE_KEYS)
    json.dumps(schema)
    record = {'team_name': 'Nova', 'status': 'reviewed', 'verdict': review.normalize_verdict(verdict('action_decision', 'tool_calling'))}
    row = review.csv_row(record)
    assert row['qualified'] == 'yes' and row['agent_stage_count'] == 2
    assert row['action_decision'] == 'yes' and row['data_parsing'] == 'no'
    assert set(row) == set(review.CSV_FIELDS)
    failed = review.csv_row({'team_name': 'X', 'status': 'error', 'verdict': None, 'error': 'boom'})
    assert failed['qualified'] == '' and failed['error'] == 'boom'


def test_final_versions_sql_quotes_the_team_filter():
    sql = review.final_versions_sql(lambda v: "'" + str(v).replace("'", "''") + "'", 'online', "o'brien")
    assert "slug='o''brien'" in sql and "observer_final_version_state" in sql
    assert 'team_filter' not in review.final_versions_sql(lambda v: repr(v), 'online', None)
