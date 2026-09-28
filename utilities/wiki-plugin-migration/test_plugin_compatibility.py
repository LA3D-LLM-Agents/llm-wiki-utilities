"""Optional real-plugin checks: WIKI_PLUGIN_ROOT=/path/to/.../plugins/llm-wiki."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

import test_migrate as fixtures

git = fixtures.git

PLUGIN = Path(os.environ.get('WIKI_PLUGIN_ROOT', '/not-configured'))
INIT = PLUGIN / 'skills/wiki-init/scripts/init-wiki.py'


@unittest.skipUnless(INIT.is_file(), 'set WIKI_PLUGIN_ROOT to audit the actual plugin')
class PluginCompatibility(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GenericMigration()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.slug = self.fixture.slug
        self.wiki = self.fixture.wiki
        for name, content in {
            f'SCHEMA_{self.slug}.md': '# Existing schema\n',
            f'index_{self.slug}.md': 'Catalog of all wiki pages, organized by category.\n',
            f'log_{self.slug}.md': '# Existing log\n',
        }.items():
            (self.wiki / name).write_text(content)
        git(self.wiki, 'add', '.')
        git(self.wiki, 'commit', '-qm', 'Existing wiki foundations')
        self.fixture.run_tool('--apply')
        self.wiki = self.root / '.llm-wiki'

    def init(self, *args):
        result = subprocess.run([sys.executable, str(INIT), *args], cwd=self.root,
                                capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)

    def test_explicit_namespace_preserves_existing_dirty_wiki(self):
        (self.wiki / 'Home.md').write_text('ongoing work')
        before = git(self.wiki, 'rev-parse', 'HEAD')
        code, result = self.init('--repo-name', self.slug, '--agent', 'codex')
        self.assertEqual(0, code)
        self.assertEqual('already-initialized', result['status'])
        self.assertEqual(before, git(self.wiki, 'rev-parse', 'HEAD'))
        self.assertEqual('ongoing work', (self.wiki / 'Home.md').read_text())

    def test_audit_renamed_origin_creates_second_namespace(self):
        # Reproduces an upstream issue; not an endorsement of this behavior.
        git(self.root, 'remote', 'add', 'origin', 'https://github.com/example/renamed-project.git')
        before = git(self.wiki, 'rev-parse', 'HEAD')
        code, result = self.init('--agent', 'codex')
        self.assertEqual(0, code)
        self.assertEqual('scaffolded', result['status'])
        self.assertNotEqual(before, git(self.wiki, 'rev-parse', 'HEAD'))
        self.assertTrue((self.wiki / 'SCHEMA_renamed-project.md').exists())
        self.assertTrue((self.wiki / f'SCHEMA_{self.slug}.md').exists())

    def test_audit_initialized_shortcut_skips_exclusion_repair(self):
        exclude = self.root / '.git/info/exclude'
        exclude.write_text('')
        code, result = self.init('--repo-name', self.slug)
        self.assertEqual(0, code)
        self.assertEqual('already-initialized', result['status'])
        check = subprocess.run(['git', '-C', str(self.root), 'check-ignore', '--quiet', '.llm-wiki/'])
        self.assertEqual(1, check.returncode)
        helper = runpy.run_path(str(PLUGIN / 'hooks/session-start.d/15-ensure-local-exclude.py'))
        helper['ensure_local_exclude'](self.root)
        git(self.root, 'check-ignore', '.llm-wiki/Home.md')
