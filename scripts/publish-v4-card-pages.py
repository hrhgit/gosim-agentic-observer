#!/usr/bin/env python3
"""Check and upload the task card pages of the hackathon cards A-D.

The pages of A-D must not be public before the competition starts, and this repository is
public, so they are kept in a private folder OUTSIDE any checkout (taskcard.<id>.v4.<lang>.md,
the same shape as web/src/content/taskcard.alpha.v4.<lang>.md). This script checks them and
uploads each one to the public 'scenarios' bucket as <slug>/public/taskcard.<lang>.md, where the
site reads it (web/src/lib/taskCardSource.ts). The bucket serves it only once the card's files
are released: scripts/configure-v4-phases.py lists every config/ and public/ file of a formal
card (timeline files excepted) for release at the competition start, so upload the pages
BEFORE that switch.

    python scripts/publish-v4-card-pages.py --pages DIR [--cards a,b,c,d] [--apply]

Checks, per card and language (dry run by default; --apply uploads only when all pass):
  * zh and en pages exist, have the same headings and tables, the shared facts of the rules
    (site, 900 s, the worked example, the four actions) and no unfilled {{...}} field;
  * hackathon wording only (no Playground wording), the weather stated as hidden, and none of the
    hidden-truth vocabulary or timeline file names;
  * the facts match the card's public inputs in the bucket: nights and dates
    (v4_night_calendar.csv), targets and required targets (targets.csv), regions
    (footprint.csv), and the false-report allowance (v4_score_config.json);
  * after the upload, each page reads back unchanged; the result shows what an anonymous visitor
    gets for it (400 until the card is released).
Uses SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_ANON_KEY from the environment.
Prints no page content.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CARDS = ('a', 'b', 'c', 'd')
LANGUAGES = ('zh', 'en')
PRACTICE_WORDS = re.compile(r"练习赛|练习场景|Playground|\bpractice\b|練習|entraînement", re.I)
HIDDEN_WORDS = re.compile(r"seed|种子|pointing offset|指向偏差|efficiency multiplier|效率乘数|window_max_fraction|magnitude|震级", re.I)
# Names of the files from which the weather and event timeline can be read (configure-v4-phases.py
# never releases them for a formal card); a page must not point at them either.
TIMELINE_NAMES = re.compile(r"v4_(bulletins|forecasts|weather_truth|events|slots|earthquake_effects|stress_events)\b|truth/", re.I)
SHARED = ('900', '1.08', '0.90', '`observe`', '`wait`', '`report`', '`finish`')
HIDDEN_WEATHER = {'zh': ('不公开', '比赛开始'), 'en': ('Hidden', 'competition starts')}


def body(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def headings(text):
    return [line for line in text.splitlines() if line.startswith('#')]


def expected_facts(inputs, language):
    """Phrases the page must contain, from the card's public inputs."""
    nights = list(csv.DictReader(io.StringIO(inputs['public/v4_night_calendar.csv'])))
    targets = list(csv.DictReader(io.StringIO(inputs['public/targets.csv'])))
    regions = {r['component_id'] for r in csv.DictReader(io.StringIO(inputs['public/footprint.csv']))}
    required = sum(1 for t in targets if str(t.get('required', '')).strip().lower() in ('1', 'true', 'yes'))
    free = int(json.loads(inputs['config/v4_score_config.json'])['reporting'].get('false_report_free_allowance', 0))
    first, last, n = nights[0]['night_date'], nights[-1]['night_date'], len(nights)
    if language == 'zh':
        facts = [f'{first} 至 {last}，共 {n} 夜', f'有 {len(targets):,} 个目标', f'其中 {required:,} 个是必观测目标', f'分为 {len(regions)} 块']
        facts.append(f'前 {free} 次误报' if free else '没有免罚次数')
    else:
        facts = [f'{first} to {last}, {n} nights', f'{len(targets):,} targets', f'{required:,} are required', f'in {len(regions)} regions']
        facts.append(f'the first {free} false reports are free' if free else 'no free false reports')
    return facts


def check_pages(pages, inputs_by_card):
    """pages: {(card, lang): markdown}; inputs_by_card: {card: {relative path: text}} or {} to skip fact checks."""
    problems = []
    for card in sorted({c for c, _ in pages} | set(inputs_by_card)):
        texts = {}
        for lang in LANGUAGES:
            if (card, lang) not in pages:
                problems.append(f'{card}: taskcard.{card}.v4.{lang}.md is missing'); continue
            text = texts[lang] = body(pages[card, lang])
            name = f'taskcard.{card}.v4.{lang}.md'
            if not re.match(rf'#\s+\S.*\b{card.upper()}\b', text.lstrip()): problems.append(f'{name}: the title does not name card {card.upper()}')
            for fact in SHARED:
                if not re.search(fact, text): problems.append(f'{name}: lacks {fact!r}')
            if '{{' in text: problems.append(f'{name}: unfilled card field')
            for label, pattern in (('Playground wording', PRACTICE_WORDS), ('hidden-truth vocabulary', HIDDEN_WORDS),
                                   ('a weather or event timeline file name', TIMELINE_NAMES)):
                match = pattern.search(text)
                if match: problems.append(f'{name}: {label} {match.group(0)!r}')
            if not all(word in text for word in HIDDEN_WEATHER[lang]): problems.append(f'{name}: does not state that the weather is hidden until the competition starts')
            for fact in expected_facts(inputs_by_card[card], lang) if card in inputs_by_card else []:
                if fact not in text: problems.append(f'{name}: does not match the public inputs ({fact!r})')
        if len(texts) == 2:
            zh, en = texts['zh'], texts['en']
            if not (len(headings(zh)) == len(headings(en)) >= 8): problems.append(f'{card}: zh and en pages have different headings')
            if zh.count('|') != en.count('|'): problems.append(f'{card}: zh and en pages have different tables')
    return problems


def read_pages(folder, cards):
    folder = folder.resolve()
    if folder == ROOT or ROOT in folder.parents:
        raise SystemExit(f'{folder} is inside the repository; keep the A-D pages in a private folder until the competition starts')
    return {(card, lang): (folder / f'taskcard.{card}.v4.{lang}.md').read_text(encoding='utf-8')
            for card in cards for lang in LANGUAGES if (folder / f'taskcard.{card}.v4.{lang}.md').exists()}


# ---------------------------------------------------------------- storage
def http(method, path, data=None, *, key='SUPABASE_SERVICE_ROLE_KEY', content_type=None):
    headers = {'apikey': os.environ['SUPABASE_ANON_KEY'], 'Authorization': 'Bearer ' + os.environ[key], 'User-Agent': 'cosmos-ops'}
    if data is not None: headers.update({'Content-Type': content_type, 'x-upsert': 'true'})
    req = urllib.request.Request(os.environ['SUPABASE_URL'] + path, data=data, method=method, headers=headers)
    for attempt in range(5):      # uploads are upserts, so a retry after a dropped connection is safe
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except OSError:
            if attempt == 4: raise
            time.sleep(2 * (attempt + 1))


def obj(name, authenticated=True):
    return f"/storage/v1/object/{'authenticated/' if authenticated else ''}scenarios/" + urllib.parse.quote(name, safe='/')


def bucket_inputs(card):
    out = {}
    for rel in ('public/v4_night_calendar.csv', 'public/targets.csv', 'public/footprint.csv', 'config/v4_score_config.json'):
        status, data = http('GET', obj(f'v4-{card}/{rel}'))
        if status != 200: raise SystemExit(f'v4-{card}/{rel} is not in the scenarios bucket (HTTP {status}); register the card first')
        out[rel] = data.decode('utf-8')
    return out


def anonymous_status(name):
    return http('GET', obj(name), key='SUPABASE_ANON_KEY')[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pages', type=Path, required=True, help='Private folder with taskcard.<id>.v4.<lang>.md')
    parser.add_argument('--cards', default=','.join(CARDS), help='Cards to publish, comma separated (a,b,c,d)')
    parser.add_argument('--apply', action='store_true', help='Upload the pages (default: check only)')
    args = parser.parse_args(argv)
    cards = [c.strip().lower() for c in args.cards.split(',') if c.strip()]
    if not cards or any(c not in CARDS for c in cards): parser.error('cards must be among a,b,c,d')
    for key in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_ANON_KEY'):
        if not os.environ.get(key): raise SystemExit(f'missing env {key}')
    pages = read_pages(args.pages, cards)
    problems = check_pages(pages, {card: bucket_inputs(card) for card in cards})
    result = {'cards': cards, 'problems': problems, 'uploaded': [], 'anonymous': {}}
    if args.apply and not problems:
        for card in cards:
            for lang in LANGUAGES:
                name = f'v4-{card}/public/taskcard.{lang}.md'
                status, _ = http('POST', obj(name, False), pages[card, lang].encode('utf-8'), content_type='text/markdown; charset=utf-8')
                if status not in (200, 201): raise SystemExit(f'upload of {name} failed (HTTP {status})')
                back = http('GET', obj(name))
                if back[0] != 200 or back[1] != pages[card, lang].encode('utf-8'): raise SystemExit(f'{name} does not read back')
                result['uploaded'].append(name)
                result['anonymous'][name] = anonymous_status(name)
    print(json.dumps(result, indent=1))
    return 2 if problems else 0


if __name__ == '__main__': sys.exit(main())
