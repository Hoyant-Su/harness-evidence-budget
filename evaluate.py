"""Compare pilot codings with the published human audit by resampling systems."""

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random

import code_system as coding
import fulltext_screen as fulltext


def canonical(value):
    return '|'.join(sorted(value.split('|'))) if value else ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness-repo', type=Path, required=True)
    parser.add_argument('--bundles', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    human = {(r['system_id'], r['dim_id']): r for r in csv.DictReader(
        (args.harness_repo / 'data/audit/human_sheet.csv').open())}
    config = json.loads((args.results / 'configuration.json').read_text())
    dims = {d['id']: d for d in coding.load_dimensions()}
    manifest = json.loads((args.bundles / 'manifest.json').read_text())
    records = []
    for condition in config['conditions']:
        for source in manifest:
            system = source['system_id']
            result = json.loads((args.results / condition / (system + '.json')).read_text())
            document = (args.bundles / condition / (system + '.txt')).read_text()
            try:
                answer = json.loads(result['message']['content'])
            except json.JSONDecodeError:
                answer = {'votes': []}
            votes = answer.get('votes', [])
            cells = votes[0].get('cells', {}) if len(votes) == 1 and votes[0].get('record_id') == system else {}
            for dim in config['dimensions']:
                check = coding.check_cell(dims[dim], cells.get(dim), fulltext._squash(document))
                state = 'unresolved' if check.invalid else ('not_reported' if check.not_reported else
                         ('coded' if check.resolved else 'unresolved'))
                reference = human[system, dim]
                value = canonical(coding.value_text(check.value)) if state == 'coded' else ''
                expected = canonical(reference['human_value']) if reference['human_state'] == 'coded' else ''
                records.append({'system_id': system, 'condition': condition, 'dimension': dim,
                    'model_state': state, 'model_value': value, 'human_state': reference['human_state'],
                    'human_value': expected, 'exact_agreement': state == reference['human_state'] and value == expected,
                    'quote_present': bool(check.quote), 'quote_valid': check.quote_verbatim,
                    'flags': '|'.join(check.flags), 'finish_reason': result['done_reason']})
    with (args.results / 'cells.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(records)
    methods = {}
    for condition in config['conditions']:
        group = [r for r in records if r['condition'] == condition]
        methods[condition] = {'cells': len(group), 'exact_agreement': sum(r['exact_agreement'] for r in group),
            'coded': sum(r['model_state'] == 'coded' for r in group),
            'not_reported': sum(r['model_state'] == 'not_reported' for r in group),
            'unresolved': sum(r['model_state'] == 'unresolved' for r in group),
            'quotes_present': sum(r['quote_present'] for r in group),
            'valid_quotes': sum(r['quote_valid'] for r in group)}
    per_system = defaultdict(dict)
    for r in records:
        per_system[r['system_id']].setdefault(r['condition'], []).append(int(r['exact_agreement']))
    differences = [sum(r['retrieval_110k']) / len(r['retrieval_110k']) -
                   sum(r['collection_110k']) / len(r['collection_110k']) for r in per_system.values()]
    rng = random.Random(20261101)
    draws = sorted(sum(rng.choices(differences, k=len(differences))) / len(differences) for _ in range(10000))
    summary = {'systems': len(manifest), 'dimensions_per_system': len(config['dimensions']),
        'larger_budget_changes_input': sum(r['larger_budget_changes_input'] for r in manifest),
        'methods': methods, 'retrieval_minus_collection': {'difference': sum(differences) / len(differences),
            'system_bootstrap_95_interval': [draws[249], draws[9749]], 'resamples': 10000, 'seed': 20261101}}
    (args.results / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
