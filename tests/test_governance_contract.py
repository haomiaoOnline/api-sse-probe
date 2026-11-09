"""对外文档必须聚焦当前功能；发布清单不得包含历史待办或敏感运行文件。"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GovernanceContractTests(unittest.TestCase):
    def test_skill_is_concise_without_release_history(self):
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf8')
        self.assertLess(len(skill.splitlines()), 135)
        for historical in ('## v2.4.1', '## v2.4.2', '## v2.3.0', '历史统一入口开发进展',
                           '三季度', '历史待办', 'v1.9.0 历史架构'):
            self.assertNotIn(historical, skill)
        for required in ('唯一正式入口', '判断依据', 'UNKNOWN', '绝不上传', '人工确认'):
            self.assertIn(required, skill)

    def test_release_manifest_omits_historical_docs_and_secrets(self):
        names = [x.strip() for x in (ROOT / 'TEAM_MANIFEST.txt').read_text().splitlines()
                 if x.strip() and not x.startswith('#')]
        self.assertIn('references/architecture.md', names)
        self.assertIn('references/assertions.md', names)
        self.assertNotIn('CHANGELOG.md', names)
        self.assertFalse(any(name.startswith(('docs/governance/', 'reports/', 'artifacts/'))
                             or 'config.local' in name for name in names))

    def test_architecture_matches_current_entry(self):
        arch = (ROOT / 'references/architecture.md').read_text(encoding='utf8')
        for name in ('unified_workflow.py', 'rule_contract.py', 'v2_rejudge.py',
                     'v2_repository.py'):
            self.assertIn(name, arch)
        self.assertNotIn('v1.9.0', arch)


if __name__ == '__main__':
    unittest.main()