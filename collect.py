"""Collect the audited repository versions for an evidence-budget experiment."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile

import requests


CUTOFF = '2026-08-31T23:59:59Z'


def session():
    client = requests.Session()
    client.trust_env = False
    client.headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    return client


def collect(row, output, upstream, pins):
    path = output / (row['system_id'] + '.json.gz')
    if path.exists() or path.with_suffix('').exists():
        return
    client = session()

    class GitHub(upstream.Gh):
        def api(self, args):
            if args[0] == 'graphql':
                fields = dict(arg.split('=', 1) for arg in args[2::2])
                query = fields.pop('query')
                response = client.post('https://api.github.com/graphql',
                                       json={'query': query, 'variables': fields}, timeout=60)
            else:
                response = client.get('https://api.github.com/' + args[0], timeout=60)
            response.raise_for_status()
            return response.json()

    repo = '/'.join(row['repo_url'].split('/')[3:5])
    pinned = re.search(r'@ ([0-9a-f]{12,40})', row['pinned_version'])
    frozen = pins.get(row['system_id'])
    if frozen:
        meta = upstream.RepoMeta(**frozen['collection_metadata'])
    elif pinned:
        response = client.get(f'https://api.github.com/repos/{repo}/commits/{pinned[1]}', timeout=60)
        if response.status_code != 200:
            record = {'system_id': row['system_id'], 'repo': repo, 'status': response.status_code,
                      'pin': pinned[1]}
            path.with_suffix('').write_text(json.dumps(record, indent=2) + '\n')
            print(row['system_id'], 'pin unavailable', response.status_code, flush=True)
            return
        commit = response.json()
        meta = upstream.RepoMeta(full=repo, sha=commit['sha'], ref='audit-pin',
                                 ref_date=commit['commit']['committer']['date'][:10])
    else:
        meta = GitHub('unused').meta(*repo.split('/'))
    url = f'https://api.github.com/repos/{repo}/tarball/{meta.sha}'
    response = client.get(url, timeout=90, stream=True)
    response.raise_for_status()
    stream = io.BytesIO()
    for chunk in response.iter_content(1024 * 1024):
        stream.write(chunk)
        if stream.tell() > 100_000_000:
            path.with_suffix('').write_text(json.dumps({'system_id': row['system_id'], 'repo': repo,
                'commit': meta.sha, 'status': 'source_archive_exceeds_100_MB'}, indent=2) + '\n')
            response.close()
            print(row['system_id'], 'source archive exceeds 100 MB', flush=True)
            return
    stream.seek(0)
    files, tree = {}, []
    with tarfile.open(fileobj=stream, mode='r:gz') as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = member.name.split('/', 1)[1]
            tree.append(name)
            if upstream.TREE_NOISE_RE.search(name):
                continue
            content = archive.extractfile(member).read()
            if b'\x00' not in content:
                files[name] = content.decode('utf-8', errors='replace')
    tree.sort()
    selected = upstream.select_code_files(tree)
    baseline = upstream.build_bundle(row['system_id'], files, meta, meta.sha, tree, selected)
    if frozen:
        baseline = re.sub(r'(?m)^fetched_at: .+$', 'fetched_at: ' + frozen['fetched_at'], baseline)
    result = {'system_id': row['system_id'], 'repo': repo, 'commit': meta.sha,
              'commit_date': meta.ref_date, 'files': files, 'tree': tree,
              'baseline': baseline, 'selected_code_files': selected}
    with gzip.open(path, 'wt') as handle:
        json.dump(result, handle)
    print(row['system_id'], len(files), len(baseline), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness-repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pins', type=Path)
    args = parser.parse_args()
    pins = {r['system_id']: r for r in json.loads(args.pins.read_text())} if args.pins else {}
    args.output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('upstream_collection', args.harness_repo / 'scripts/fetch_repos.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rows = list(csv.DictReader((args.harness_repo / 'data/audit/human_sheet.csv').open()))
    unique = {row['system_id']: {key: row[key] for key in ['system_id', 'system_name', 'repo_url',
              'pinned_version', 'paper_ids', 'paper_refs']} for row in rows}
    (args.output / 'sample.json').write_text(json.dumps(list(unique.values()), indent=2) + '\n')
    selected = [row for row in unique.values() if row['repo_url'] and (not pins or row['system_id'] in pins)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda row: collect(row, args.output, module, pins), selected))


if __name__ == '__main__':
    main()
