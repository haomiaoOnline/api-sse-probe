#!/usr/bin/env python3
"""Static, side-effect-free v2.4.3 Skill governance check.

No credentials, reports, or network connections are opened.
Designed for source root AND installed manifest-only Skill directories.
"""
import argparse
import json
import re
import sys
from pathlib import Path


def validate(root):
    root = Path(root).resolve()
    errors = []
    def read(relative):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            errors.append('missing_or_symlinked:' + relative)
            return ''
        return path.read_text(encoding='utf-8')

    skill = read('SKILL.md')
    manifest = read('TEAM_MANIFEST.txt')
    current = read('docs/governance/current-v2.4.3.md')
    history = [read('docs/governance/README.md'),
               read('docs/governance/unified-entry-migration.md'),
               read('references/architecture.md')]
    acceptance = read('docs/governance/acceptance-matrix.md')
    feedback = read('docs/governance/feedback-loop.md')
    root_script = read('scripts/probe.py')
    compat_script = read('scripts/probe_v2.py')

    version = re.search(r'(?m)^  version: "([^"]+)"$', skill)
    if not version or version.group(1) != '2.4.3':
        errors.append('unexpected_root_skill_version')
    if '根目录' not in skill or 'current-v2.4.3.md' not in skill:
        errors.append('missing_authoritative_source_pointer')
    if 'main' not in current or 'baseline/pre-governance-v2.4.3' not in current:
        errors.append('missing_rollback_contract')
    for required in ('UNKNOWN', 'DISPATCH_INTENT', 'CAPTURED', '未完成',
                     'probe.py', 'probe_v2.py', '尚未验证', '人工确认'):
        if required not in current:
            errors.append('feature_map_missing:' + required)
    for i,document in enumerate(history):
        if '历史' not in document[:350] or 'current-v2.4.3.md' not in document[:350]:
            errors.append('historical_document_unlabelled:' + str(i))
    if 'GOV-010' not in acceptance or '尚未验证' not in acceptance:
        errors.append('acceptance_matrix_incomplete')
    for required in ('FB-007', 'FB-008', 'FB-009', '正反', '回滚'):
        if required not in feedback:
            errors.append('feedback_missing:' + required)
    if 'def main(' not in root_script or 'from lib.unified_workflow import main' not in root_script:
        errors.append('unified_default_entry_unavailable')
    if 'from lib.v2_engine import' not in compat_script:
        errors.append('legacy_v2_compatibility_entry_unavailable')

    names = [x.strip() for x in manifest.splitlines() if x.strip() and not x.startswith('#')]
    expected = {'SKILL.md', 'TEAM_MANIFEST.txt', 'scripts/probe.py',
                'scripts/probe_v2.py', 'scripts/check_skill_contract.py',
                'docs/governance/current-v2.4.3.md',
                'tests/test_current_skill_contract.py'}
    if len(set(names)) != len(names):
        errors.append('duplicate_manifest_entry')
    for name in sorted(expected - set(names)):
        errors.append('manifest_missing:' + name)
    for name in names:
        if name.startswith(('reports/', 'artifacts/', 'api-sse-probe/')) or 'config.local' in name:
            errors.append('unsafe_manifest_entry:' + name)
        elif not (root / name).is_file():
            errors.append('unresolved_manifest_file:' + name)
    # The pre-existing nested source copy is NOT a default source.
    # Its absence in the portable package is expected and valid.
    return {'status': 'PASS' if not errors else 'FAIL',
            'source': 'root/SKILL.md',
            'version': version.group(1) if version else None,
            'errors': errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline Skill source/manifest/governance consistency check')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    result = validate(args.root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result['errors'] else 1


if __name__ == '__main__':
    sys.exit(main())