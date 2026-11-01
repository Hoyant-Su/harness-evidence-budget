"""Run the frozen HARNESS-DB coding prompt on the prepared repository conditions."""

import argparse
import csv
import json
from pathlib import Path

import httpx
import code_system as coding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness-repo', type=Path, required=True)
    parser.add_argument('--bundles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    audit = list(csv.DictReader((args.harness_repo / 'data/audit/human_sheet.csv').open()))
    ids = sorted({r['dim_id'] for r in audit})
    dims = [d for d in coding.load_dimensions() if d['id'] in ids]
    system = coding.system_prompt(dims)
    client = httpx.Client(base_url=args.endpoint, timeout=1800)
    config = {'model': args.model, 'server': client.get('/api/version').json(),
              'options': {'temperature': 0, 'seed': 20261101, 'num_ctx': 131072, 'num_predict': 2048},
              'think': False, 'dimensions': ids, 'scope': 'repository-only pilot',
              'conditions': ['collection_110k', 'collection_440k', 'retrieval_110k']}
    (args.output / 'configuration.json').write_text(json.dumps(config, indent=2) + '\n')
    (args.output / 'system_prompt.txt').write_text(system)
    for record in json.loads((args.bundles / 'manifest.json').read_text()):
        previous = {}
        for condition in config['conditions']:
            out = args.output / condition
            out.mkdir(exist_ok=True)
            path = out / (record['system_id'] + '.json')
            document = (args.bundles / condition / (record['system_id'] + '.txt')).read_text()
            if path.exists():
                previous[document] = json.loads(path.read_text())
                continue
            if document in previous:
                result = {**previous[document], 'reused_identical_input': True}
            else:
                item = {'id': record['system_id'], 'dims': ids, 'document': document,
                        'header': {'repo_url': 'https://github.com/' + record['repo'], 'version': record['commit']}}
                response = client.post('/api/chat', json={'model': args.model, 'think': False, 'stream': False,
                    'messages': [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': coding.pass_a_prompt([item])}],
                    'format': coding.batch_schema(ids), 'options': config['options']})
                response.raise_for_status()
                result = response.json()
                assert result['prompt_eval_count'] < 131072 - 2048
                previous[document] = result
            path.write_text(json.dumps(result, indent=2) + '\n')
            print(record['system_id'], condition, result['done_reason'], result['prompt_eval_count'], flush=True)


if __name__ == '__main__':
    main()
