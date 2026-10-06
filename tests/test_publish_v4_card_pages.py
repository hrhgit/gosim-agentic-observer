"""scripts/publish-v4-card-pages.py: the checks on the private A-D task card pages and their upload to
the scenarios bucket, with synthetic pages and inputs (the real pages stay out of this public repository)."""
import importlib.util
import json
from pathlib import Path
import re

import pytest

# CI runs these through tests/test_configure_v4_phases.py (star import), so every module-level name
# that test file could clash with is listed here explicitly.
__all__ = ['page_bucket', 'test_consistent_pages_pass', 'test_each_check_catches_its_mistake',
           'test_the_report_rule_must_match_the_score_config', 'test_pairs_and_titles', 'test_pages_inside_the_repository_are_refused',
           'test_dry_run_uploads_nothing_and_apply_uploads_both_languages', 'test_apply_with_problems_uploads_nothing',
           'test_released_pages_are_not_timeline_files']

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / 'web' / 'src' / 'content'


def load_publisher():
    spec = importlib.util.spec_from_file_location('publish_v4_card_pages', ROOT / 'scripts/publish-v4-card-pages.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def card_inputs(*, free=2, required=3):
    """A tiny synthetic card: 3 nights, 5 targets, 2 regions."""
    targets = 'target_id,ra_deg,dec_deg,target_class,feature_flux,science_weight,required\n' + ''.join(
        f'T{i},1.0,-1.0,ELG,0.5,1.0,{"true" if i < required else "false"}\n' for i in range(5))
    calendar = 'night_id,night_date\n' + ''.join(f'N{d},2030-01-0{d}\n' for d in (1, 2, 3))
    footprint = 'component_id,vertex_index,ra_deg,dec_deg\nC00,0,1,1\nC00,1,2,2\nC01,0,5,5\n'
    reporting = {'correct_reward': 100, 'false_penalty': -150}
    if free: reporting['false_report_free_allowance'] = free
    return {'public/v4_night_calendar.csv': calendar, 'public/targets.csv': targets, 'public/footprint.csv': footprint,
            'config/v4_score_config.json': json.dumps({'reporting': reporting})}


def card_page(card, lang, *, free=2, required=3):
    """A hackathon card page in the shape of the practice pages, with the facts of card_inputs()."""
    text = re.sub(r'<!--.*?-->\n*', '', (CONTENT / f'taskcard.template.v4.{lang}.md').read_text(encoding='utf-8'), flags=re.S)
    zh = lang == 'zh'
    fields = {
        'SYMBOL': card.upper(), 'ONE_SENTENCE_STORY': 'Test.', 'START_DATE': '2030-01-01',
        'END_DATE': '2030-01-03', 'NIGHTS': '3', 'AREA_DEG2': '10', 'COMPONENTS': '2', 'TARGETS': '5', 'REQUIRED': str(required),
        'WALLCLOCK': '900',
        'WEATHER': '不公开。比赛开始时发布公开输入。' if zh else 'Hidden. The public inputs are published when the competition starts.',
        'EXTRA_MESSAGES': ('前 2 次误报不扣分' if free else '没有免罚次数') if zh else
                          ('the first 2 false reports are free' if free else 'no free false reports'),
        'TRY_IT': '提交。' if zh else 'Submit.',
    }
    for key, value in fields.items():
        text = text.replace('{{' + key + '}}', value)
    return text


def good_pages(card='a', **kw):
    return {(card, lang): card_page(card, lang, **kw) for lang in ('zh', 'en')}


def test_consistent_pages_pass():
    mod = load_publisher()
    assert mod.check_pages(good_pages(), {'a': card_inputs()}) == []
    assert mod.check_pages(good_pages(free=0), {'a': card_inputs(free=0)}) == []


@pytest.mark.parametrize('lang', ['zh', 'en'])
@pytest.mark.parametrize('change, problem', [
    (lambda t: t.replace('0.90', '0.9O'), "lacks '0.90'"),
    (lambda t: t + '\n{{TRY_IT}}\n', 'unfilled card field'),
    (lambda t: t + '\nPlayground\n', 'Playground wording'),
    (lambda t: t + '\nseed\n', 'hidden-truth vocabulary'),
    (lambda t: t + '\npublic/v4_bulletins.jsonl\n', 'timeline file name'),
    (lambda t: t + '\ntruth/v4_x.csv\n', 'timeline file name'),
    (lambda t: t.replace('不公开', '公开').replace('Hidden', 'Public'), 'weather is hidden'),
    (lambda t: t.replace('2030-01-03', '2030-01-04'), 'does not match the public inputs'),
    (lambda t: t.replace('其中 3 个', '其中 4 个').replace('3 are required', '4 are required'), 'does not match the public inputs'),
    (lambda t: t.replace('分为 2 块', '分为 3 块').replace('in 2 regions', 'in 3 regions'), 'does not match the public inputs'),
])
def test_each_check_catches_its_mistake(lang, change, problem):
    mod = load_publisher()
    pages = good_pages()
    pages['a', lang] = change(pages['a', lang])
    problems = mod.check_pages(pages, {'a': card_inputs()})
    assert any(problem in p and f'.{lang}.md' in p for p in problems), problems


def test_the_report_rule_must_match_the_score_config():
    mod = load_publisher()
    # a page that promises free false reports on a card without any allowance (and the reverse)
    assert any('没有免罚次数' in p for p in mod.check_pages(good_pages(), {'a': card_inputs(free=0)}))
    assert any('the first 2 false reports are free' in p for p in mod.check_pages(good_pages(free=0), {'a': card_inputs()}))


def test_pairs_and_titles():
    mod = load_publisher()
    pages = good_pages()
    del pages['a', 'en']
    assert 'a: taskcard.a.v4.en.md is missing' in mod.check_pages(pages, {'a': card_inputs()})
    pages = good_pages()
    pages['a', 'en'] = pages['a', 'en'] + '\n## Extra\n'
    assert 'a: zh and en pages have different headings' in mod.check_pages(pages, {'a': card_inputs()})
    pages = good_pages()
    pages['a', 'zh'] = pages['a', 'zh'] + '\n| x |\n'
    assert 'a: zh and en pages have different tables' in mod.check_pages(pages, {'a': card_inputs()})
    pages = {('b', lang): text for (_, lang), text in good_pages().items()}   # the page of A filed as B
    assert any('does not name card B' in p for p in mod.check_pages(pages, {'b': card_inputs()}))


def test_pages_inside_the_repository_are_refused(tmp_path):
    mod = load_publisher()
    with pytest.raises(SystemExit, match='inside the repository'):
        mod.read_pages(CONTENT, ['a'])
    for lang, text in good_pages().items():
        (tmp_path / f'taskcard.a.v4.{lang[1]}.md').write_text(text, encoding='utf-8')
    assert set(mod.read_pages(tmp_path, ['a', 'b'])) == {('a', 'zh'), ('a', 'en')}


@pytest.fixture
def page_bucket(monkeypatch):
    """The scenarios bucket of one card: its public inputs; anonymous reads are refused (unreleased)."""
    mod = load_publisher()
    for key in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_ANON_KEY'):
        monkeypatch.setenv(key, 'https://example.invalid' if key == 'SUPABASE_URL' else 'x')
    store = {f'v4-a/{rel}': text.encode() for rel, text in card_inputs().items()}
    calls = []

    def http(method, path, data=None, *, key='SUPABASE_SERVICE_ROLE_KEY', content_type=None):
        name = path.split('/scenarios/', 1)[1]
        calls.append((method, name, key, content_type))
        if key == 'SUPABASE_ANON_KEY': return 400, b''
        if method == 'POST': store[name] = data; return 200, b''
        return (200, store[name]) if name in store else (400, b'')
    mod.http = http
    return mod, store, calls


def test_dry_run_uploads_nothing_and_apply_uploads_both_languages(page_bucket, tmp_path, capsys):
    mod, store, calls = page_bucket
    for (card, lang), text in good_pages().items():
        (tmp_path / f'taskcard.{card}.v4.{lang}.md').write_text(text, encoding='utf-8')
    assert mod.main(['--pages', str(tmp_path), '--cards', 'a']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['problems'] == [] and out['uploaded'] == [] and not any(c[0] == 'POST' for c in calls)
    assert mod.main(['--pages', str(tmp_path), '--cards', 'a', '--apply']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['uploaded'] == ['v4-a/public/taskcard.zh.md', 'v4-a/public/taskcard.en.md']
    assert out['anonymous'] == {name: 400 for name in out['uploaded']}
    assert store['v4-a/public/taskcard.en.md'] == good_pages()['a', 'en'].encode()
    assert {c[3] for c in calls if c[0] == 'POST'} == {'text/markdown; charset=utf-8'}
    assert 'Paranal' not in json.dumps(out)                       # no page content printed


def test_apply_with_problems_uploads_nothing(page_bucket, tmp_path, capsys):
    mod, store, calls = page_bucket
    for (card, lang), text in good_pages().items():
        (tmp_path / f'taskcard.{card}.v4.{lang}.md').write_text(text.replace('3 are required', '4 are required'), encoding='utf-8')
    assert mod.main(['--pages', str(tmp_path), '--cards', 'a', '--apply']) == 2
    out = json.loads(capsys.readouterr().out)
    assert out['problems'] and out['uploaded'] == [] and not any(c[0] == 'POST' for c in calls)


def test_released_pages_are_not_timeline_files():
    # configure-v4-phases.py releases a formal card's config/ and public/ files except timeline files;
    # the uploaded pages must be among the released ones.
    spec = importlib.util.spec_from_file_location('configure_v4_phases', ROOT / 'scripts/configure-v4-phases.py')
    phases = importlib.util.module_from_spec(spec); spec.loader.exec_module(phases)
    for lang in ('zh', 'en'):
        assert not phases.timeline_file(f'public/taskcard.{lang}.md')
