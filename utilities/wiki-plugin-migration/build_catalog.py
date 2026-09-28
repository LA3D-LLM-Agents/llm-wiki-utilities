#!/usr/bin/env python3
"""Build an offline catalog from committed upstream template checkouts."""
import argparse
import json
from pathlib import Path
from template_cleanup import reference_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repositories', nargs='+', type=Path)
    parser.add_argument('--all-history', action='store_true', help='include all revisions reachable from each HEAD')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('template-catalog.json'))
    args = parser.parse_args()
    catalog = {'version': 1, 'source': '', 'commits': [], 'files': {}}
    for repo in args.repositories:
        extra = reference_catalog(repo, all_history=args.all_history)
        catalog['source'] = extra['source']
        catalog['commits'].extend(extra['commits'])
        for name, variants in extra['files'].items():
            catalog['files'].setdefault(name, []).extend(variants)
    catalog['commits'] = list(dict.fromkeys(catalog['commits']))
    for name in catalog['files']:
        catalog['files'][name] = sorted(set(catalog['files'][name]))
    args.output.write_text(json.dumps(catalog, indent=2) + '\n')
    print(f"Catalog: {len(catalog['commits'])} upstream commits; {len(catalog['files'])} paths")


if __name__ == '__main__':
    main()
