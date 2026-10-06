"""configure-v4-phases.py --preview: the public test runs a dedicated public v4 card."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location('configure_v4_phases', ROOT / 'scripts/configure-v4-phases.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    phases = {slug: {'id': f'00000000-0000-4000-8000-00000000000{i}', 'slug': slug, 'is_active': True,
                     'counts_for_final': slug == module.FINAL, 'leaderboard_mode': 'hidden', 'daily_limit': 10,
                     'settings': {'sealed': slug == module.FINAL, 'runtime_seconds': 1800, 'board_layout': 'overall'},
                     'scenarios': []}
              for i, slug in enumerate((module.PRACTICE, module.FORMAL, module.FINAL), 1)}
    card = lambda slug, i, **extra: {'id': f'00000000-0000-4000-9000-{i:012d}', 'slug': slug, 'is_active': True,
                                     'public_flags': False, 'all_public': False, 'contract': 'v4-score-v1',
                                     'global_wallclock_seconds': 900, 'bundle': True, 'listed': False,
                                     'public_files': 0, 'files': [], 'links': [], **extra}
    cards = {s: card(s, i) for i, s in enumerate(['v4-alpha', 'v4-beta', 'v4-a', 'v4-b', 'v4-c', 'v4-d',
                                                  'v4-e', 'v4-f', 'v4-g', 'v4-h'])}
    cards['v4-alpha'].update(public_flags=True, all_public=True)
    cards['v4-public-test'] = card('v4-public-test', 99, public_flags=True, all_public=True, global_wallclock_seconds=300)
    cards['formal-a'] = card('formal-a', 98, contract='challenge-score-v3', public_flags=True, all_public=True)
    monkeypatch.setattr(module, 'phase', lambda slug: phases[slug])
    monkeypatch.setattr(module, 'scenarios', lambda slugs: {s: cards[s] for s in slugs})
    monkeypatch.setattr(module, 'activity', lambda: {'jobs': 0, 'batches': 0, 'open_snapshots': 0, 'missing_migrations': []})
    module.prep_fixture = {'scenario': 'formal-a', 'phase': 'practice-projects', 'enabled': True, 'phase_colocated': False}
    monkeypatch.setattr(module, 'query', lambda sql: [module.prep_fixture])
    module.cards_fixture = cards
    return module


def plan(script, preview, practice_mode='replace'):
    parser_args = script.argparse.Namespace(
        practice=['v4-alpha', 'v4-beta'], formal=['v4-a', 'v4-b', 'v4-c', 'v4-d'], final=['v4-e', 'v4-f', 'v4-g', 'v4-h'],
        practice_mode=practice_mode, runtime=900, practice_runtime=900, daily=4, preview=preview)
    return script.plan_forward(parser_args)


def test_dedicated_public_v4_card_is_accepted_as_preview(script):
    result, sql = plan(script, 'v4-public-test')
    assert result['problems'] == [], result['problems']
    assert "update private.observer_preparation_config set scenario_id='00000000-0000-4000-9000-000000000099'" in sql


@pytest.mark.parametrize('preview,needle', [
    ('v4-alpha', 'dedicated public test card'),
    ('v4-a', 'dedicated public test card'),
    ('formal-a', 'not a v4 card'),
])
def test_competition_or_v3_cards_are_refused_as_preview(script, preview, needle):
    result, _ = plan(script, preview)
    assert any(needle in p for p in result['problems']), result['problems']


def test_a_non_public_card_is_refused_and_a_long_one_warned(script):
    script.cards_fixture['v4-public-test'].update(all_public=False, global_wallclock_seconds=900)
    result, _ = plan(script, 'v4-public-test')
    assert any('public weather, forecasts and events' in p for p in result['problems'])
    assert any('capped at 300 s' in w for w in result['warnings'])


def test_the_public_test_phase_must_end_up_colocated(script):
    # practice-projects is switched to colocated with the practice cards ...
    assert plan(script, 'v4-public-test')[0]['problems'] == []
    # ... but not when the practice phase is skipped and it is not colocated already.
    result, _ = plan(script, 'v4-public-test', practice_mode='skip')
    assert any('not colocated' in p for p in result['problems']), result['problems']
    script.prep_fixture['phase_colocated'] = True
    assert plan(script, 'v4-public-test', practice_mode='skip')[0]['problems'] == []
