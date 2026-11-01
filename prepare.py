"""Prepare a repository-only pilot with fixed collection and retrieval budgets."""

import argparse
from collections import Counter
import csv
import gzip
import json
import math
from pathlib import Path
import re


def tokens(text):
    return re.findall(r'[a-z]+', re.sub(r'([a-z])([A-Z])', r'\1 \2', text).lower())


def retrieve(files, queries, budget):
    chunks = []
    for name, text in sorted(files.items()):
        lines = text.splitlines(keepends=True)
        for start in range(0, len(lines), 200):
            body = ''.join(lines[start:start + 200])
            if body.strip():
                chunks.append({'path': name, 'line': start + 1, 'body': body})
    counts = [Counter(tokens(c['path'] + '\n' + c['body'])) for c in chunks]
    lengths = [sum(c.values()) for c in counts]
    mean = sum(lengths) / len(lengths)
    frequency = Counter(t for c in counts for t in c)
    rankings = []
    for query in queries:
        terms = set(tokens(query))
        scores = [sum(math.log(1 + (len(chunks) - frequency[t] + .5) / (frequency[t] + .5))
                      * c[t] * 2.5 / (c[t] + 1.5 * (.25 + .75 * length / mean))
                      for t in terms if c[t]) for c, length in zip(counts, lengths)]
        rankings.append(sorted(range(len(chunks)), key=lambda i: (-scores[i], chunks[i]['path'], chunks[i]['line'])))
    chosen, blocks, selected = set(), [], []
    used = 0
    for rank in range(len(chunks)):
        for order in rankings:
            index = order[rank]
            if index in chosen:
                continue
            chosen.add(index)
            chunk = chunks[index]
            block = f"\n### {chunk['path']}:{chunk['line']}\n{chunk['body']}"
            available = budget - used
            if available <= 0:
                return ''.join(blocks), selected
            blocks.append(block[:available])
            selected.append({'path': chunk['path'], 'line': chunk['line'], 'characters': len(block[:available])})
            used += len(block[:available])
    return ''.join(blocks), selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness-repo', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((args.harness_repo / 'data/audit/human_sheet.csv').open()))
    definitions = {r['dim_id']: r['dim_name'] + '\n' + r['value_glosses'] + '\n' + r['decision_rule'] for r in rows}
    queries = [definitions[key] for key in sorted(definitions)]
    manifest = []
    for path in sorted(args.sources.glob('*.json.gz')):
        with gzip.open(path, 'rt') as handle:
            source = json.load(handle)
        header = f"SYSTEM {source['system_id']}\nrepo: {source['repo']}\ncommit: {source['commit']}\n"
        baseline = header + source['baseline']
        retrieval, selected = retrieve(source['files'], queries, 110000 - len(header))
        conditions = {'collection_110k': baseline[:110000], 'collection_440k': baseline[:440000],
                      'retrieval_110k': header + retrieval}
        for condition, text in conditions.items():
            folder = args.output / condition
            folder.mkdir(exist_ok=True)
            (folder / (source['system_id'] + '.txt')).write_text(text)
        record = {'system_id': source['system_id'], 'repo': source['repo'], 'commit': source['commit'],
                  'source_files': len(source['files']), 'baseline_selected_files': source['selected_code_files'],
                  'characters': {key: len(text) for key, text in conditions.items()},
                  'larger_budget_changes_input': conditions['collection_110k'] != conditions['collection_440k'],
                  'retrieved_chunks': selected}
        manifest.append(record)
        print(record['system_id'], record['characters'], flush=True)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
