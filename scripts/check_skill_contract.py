#!/usr/bin/env python3
"""只读 Skill 发布契约检查：当前文档、完整源码、打包清单与隐私边界。"""
import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED = {
    'SKILL.md', 'README.md', 'TEAM_MANIFEST.txt', 'config.example.json',
    'scripts/probe.py', 'scripts/lib/unified_workflow.py',
    'scripts/lib/rule_contract.py', 'scripts/lib/v2_rejudge.py',
    'scripts/lib/feishu_transport.py', 'scripts/install.py',
    'scripts/check_skill_contract.py', 'references/architecture.md',
    'references/assertions.md', 'references/configuration.md',
}
FORBIDDEN_PREFIXES = ('reports/', 'artifacts/', 'api-sse-probe/', 'docs/governance/')
FORBIDDEN_TOKENS = ('config.local', '.env', 'secret/', 'credentials/', 'CHANGELOG.md')


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
    read('README.md')
    read('references/architecture.md')
    read('scripts/probe.py')
    read('scripts/lib/unified_workflow.py')

    version = re.search(r'(?m)^  version: "([^"]+)"$', skill)
    if not version or version.group(1) != '2.4.3':
        errors.append('unexpected_root_skill_version')
    if 'scripts/probe.py' not in skill or '判断依据' not in skill or '安全' not in skill:
        errors.append('current_execution_contract_missing')
    if re.search(r'(?im)^#{1,3}\s*(?:v[12]\.\d|历史|版本历程|待办|TODO)', skill):
        errors.append('historical_sections_in_skill')
    names = [line.strip() for line in manifest.splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if len(names) != len(set(names)):
        errors.append('duplicate_manifest_entry')
    for missing in sorted(REQUIRED - set(names)):
        errors.append('manifest_missing:' + missing)
    for name in names:
        if (name.startswith(FORBIDDEN_PREFIXES)
                or any(token in name for token in FORBIDDEN_TOKENS)):
            errors.append('unsafe_or_historical_manifest_entry:' + name)
        elif not (root / name).is_file() or (root / name).is_symlink():
            errors.append('missing_manifest_file:' + name)
    return {'status': 'PASS' if not errors else 'FAIL',
            'source': 'root/SKILL.md',
            'version': version.group(1) if version else None,
            'errors': errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline current Skill release check')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    result = validate(args.root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())