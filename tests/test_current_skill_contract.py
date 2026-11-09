"""当前 Skill 说明与安全发布契约；不访问网络或私人配置。"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('skill_contract', ROOT / 'scripts/check_skill_contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class ContractTests(unittest.TestCase):
    def test_root_skill_is_current_and_packaged(self):
        outcome = contract.validate(ROOT)
        self.assertEqual(outcome['status'], 'PASS', outcome['errors'])
        self.assertEqual(outcome['source'], 'root/SKILL.md')
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf8')
        self.assertIn('scripts/probe.py', skill)
        self.assertIn('判断依据', skill)
        self.assertNotIn('## v2.4.2', skill)
        self.assertNotIn('历史版本', skill)

    def test_tamper_version_and_inject_private_file_rejected(self):
        names = [x.strip() for x in (ROOT / 'TEAM_MANIFEST.txt').read_text().splitlines()
                 if x.strip() and not x.startswith('#')]
        with tempfile.TemporaryDirectory() as directory:
            dst = Path(directory)
            for name in names:
                p = dst / name
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, p)
            self.assertEqual(contract.validate(dst)['status'], 'PASS')
            skill = dst / 'SKILL.md'
            original = skill.read_text(encoding='utf8')
            skill.write_text(original.replace('version: "2.5.0"', 'version: "1.0.0"'))
            self.assertIn('unexpected_root_skill_version', contract.validate(dst)['errors'])
            skill.write_text(original)
            (dst / 'TEAM_MANIFEST.txt').write_text(
                (dst / 'TEAM_MANIFEST.txt').read_text() + 'config.local.json\n')
            self.assertTrue(any('unsafe_or_historical_manifest_entry' in x
                                for x in contract.validate(dst)['errors']))

    def test_no_network_or_credentials(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')) as network:
            result = contract.validate(ROOT)
        network.assert_not_called()
        self.assertEqual(result['status'], 'PASS', result['errors'])


if __name__ == '__main__':
    unittest.main()