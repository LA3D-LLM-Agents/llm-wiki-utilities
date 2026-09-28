import base64
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import migrate as m
from template_cleanup import HOOKS

HERE = Path(__file__).parent
CATALOG = json.loads((HERE / 'template-catalog.json').read_text())

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)

def init(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Migration Test')
    git(root, 'config', 'user.email', 'migration@example.invalid')

def upstream(name, slug):
    return base64.b64decode(CATALOG['files'][name][-1]).replace(b'{{REPO_NAME}}', slug.encode()).replace(b'${REPO_NAME}', slug.encode())

class GenericMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'checkout renamed with spaces'
        init(self.root)
        self.slug = 'ocean_research-42'
        self.wiki = self.root / f'wiki/{self.slug}.wiki'
        self.write('.gitignore', b'wiki/*.wiki/\n# custom\nprivate-data/\n')
        for name in ['llm-wiki.md', 'wiki/init-wiki.sh', '.cursor/rules/wiki-as-memory.mdc']:
            self.write(name, upstream(name, self.slug))
        for name, template in HOOKS.items():
            self.write(name, upstream(template, self.slug))
        self.write('CLAUDE.md', f'# Project\nKeep custom instructions.\nMemory: wiki/{self.slug}.wiki/\n'.encode())
        self.settings = {'permissions': {'allow': ['Bash(pytest)']}, 'env': {'KEEP': 'yes'}, 'hooks': {
            'SessionStart': [{'matcher': 'startup', 'hooks': [
                {'type': 'command', 'command': '.claude/hooks/session-start.sh'},
                {'type': 'command', 'command': 'echo custom'}]}]}}
        self.write('.claude/settings.json', json.dumps(self.settings).encode())
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'project')
        init(self.wiki)
        (self.wiki / 'Home.md').write_text('memory')
        git(self.wiki, 'add', '.')
        git(self.wiki, 'commit', '-qm', 'wiki')
        git(self.wiki, 'remote', 'add', 'origin', 'https://example.invalid/project.wiki.git')
        (self.wiki / 'Home.md').write_text('dirty memory')
        (self.wiki / 'draft.md').write_text('staged memory')
        git(self.wiki, 'add', 'draft.md')
        (self.wiki / 'scratch').write_bytes(b'untracked')

    def write(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def run_tool(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.main([str(self.root), '--backup-dir', str(self.base / 'backups'), *args])

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and '.git' not in p.relative_to(self.root).parts}

    def test_full_bundled_template_inventory(self):
        from template_cleanup import owned
        for name in CATALOG['files']:
            if owned(name):
                self.write(name, upstream(name, self.slug))
        self.run_tool('--apply')
        self.assertTrue((self.root / '.llm-wiki/Home.md').exists())
        self.assertFalse((self.root / 'wiki/init-wiki.sh').exists())
        self.assertFalse((self.root / 'scripts/update-from-template.sh').exists())

    def test_local_settings_and_permissions(self):
        self.write('.claude/settings.local.json', json.dumps(self.settings).encode())
        self.run_tool('--apply')
        data = json.loads((self.root / '.claude/settings.local.json').read_text())
        self.assertEqual(self.settings['permissions'], data['permissions'])
        self.assertEqual('echo custom', data['hooks']['SessionStart'][0]['hooks'][0]['command'])

    def test_custom_script_dependency_blocks(self):
        self.write('scripts/custom.sh', b'bash wiki/init-wiki.sh\n')
        with self.assertRaisesRegex(m.MigrationError, 'retained instructions'):
            self.run_tool('--apply')

    def test_stock_permissions_are_removed_but_custom_entries_survive(self):
        self.write('wiki/agents/claude-code/setup.sh', upstream('wiki/agents/claude-code/setup.sh', self.slug))
        self.write('scripts/kg/build-graph.sh', upstream('scripts/kg/build-graph.sh', self.slug))
        settings = json.loads(upstream('.claude/settings.json.template', self.slug))
        settings['permissions']['allow'].extend(['Bash(pytest *)', 'Read(./research/**)'])
        settings['permissions']['deny'] = ['Bash(rm *)']
        settings['permissions']['ask'] = ['Bash(curl *)']
        settings['env'] = {'KEEP': 'yes'}
        for name in ('.claude/settings.json', '.claude/settings.local.json'):
            self.write(name, json.dumps(settings).encode())
        self.run_tool('--apply')
        for name in ('.claude/settings.json', '.claude/settings.local.json'):
            actual = json.loads((self.root / name).read_text())
            self.assertEqual(['Bash(.venv/bin/python *)', 'Bash(uv run python *)',
                              'Bash(pytest *)', 'Read(./research/**)'], actual['permissions']['allow'])
            self.assertEqual(['Bash(rm *)'], actual['permissions']['deny'])
            self.assertEqual(['Bash(curl *)'], actual['permissions']['ask'])
            self.assertEqual({'KEEP': 'yes'}, actual['env'])
        before = self.snapshot()
        self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())

    def test_custom_legacy_permission_is_not_silently_removed(self):
        self.settings['permissions']['allow'].append(f'Bash(git -C wiki/{self.slug}.wiki push *)')
        self.write('.claude/settings.json', json.dumps(self.settings).encode())
        before = self.snapshot()
        with self.assertRaisesRegex(m.MigrationError, 'retained instructions'):
            self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())

    def install_feature_fixture(self):
        for name in CATALOG['files']:
            if name.startswith('features/agent-comms/code/'):
                self.write('scripts/agent-comms/' + name.split('/code/', 1)[1], upstream(name, self.slug))
        self.write('.github/workflows/agent-comms.yml', upstream('features/agent-comms/ci/agent-comms.yml', self.slug))
        self.write('.claude/rules/feature-agent-comms.md', upstream('features/agent-comms/rule.md', self.slug))
        self.write('.claude/commands/ask.md', upstream('.claude/commands/ask.md', self.slug))
        section = upstream('features/agent-comms/CLAUDE.section.md', self.slug).rstrip(b'\n')
        self.write('CLAUDE.md', b'# Custom project\nKeep this.\n<!-- feature:agent-comms -->\n' + section + b'\n<!-- /feature:agent-comms -->\n')
        self.write('.features-enabled', b'other-feature\nagent-comms\n')
        (self.wiki / 'Card_project.md').write_text('preserve federation identity')

    def test_feature_replaced_without_touching_federation_identity(self):
        self.install_feature_fixture()
        self.run_tool('--apply')
        self.assertEqual(b'# Custom project\nKeep this.\n\n', (self.root / 'CLAUDE.md').read_bytes())
        self.assertFalse((self.root / 'scripts/agent-comms/ask.sh').exists())
        self.assertFalse((self.root / 'scripts/agent-comms/enroll.sh').exists())
        self.assertFalse((self.root / '.claude/commands/ask.md').exists())
        self.assertFalse((self.root / '.claude/rules/feature-agent-comms.md').exists())
        self.assertFalse((self.root / '.github/workflows/agent-comms.yml').exists())
        self.assertEqual(b'other-feature\n', (self.root / '.features-enabled').read_bytes())
        self.assertEqual('preserve federation identity', (self.root / '.llm-wiki/Card_project.md').read_text())

    def test_custom_feature_script_blocks_atomically(self):
        self.install_feature_fixture()
        self.write('scripts/agent-comms/ask.sh', b'custom implementation')
        before = self.snapshot()
        with self.assertRaisesRegex(m.MigrationError, 'ask.sh'):
            self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())

    def test_custom_feature_block_blocks_even_without_path_references(self):
        self.install_feature_fixture()
        self.write('CLAUDE.md', b'<!-- feature:agent-comms -->\nMy custom routing policy\n<!-- /feature:agent-comms -->')
        before = self.snapshot()
        with self.assertRaisesRegex(m.MigrationError, 'customized agent-comms block'):
            self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())

    def test_local_exclude_preserves_shared_gitignore(self):
        before = (self.root / '.gitignore').read_bytes()
        exclude = self.root / '.git/info/exclude'
        exclude.write_bytes(b'# custom local rules\nprivate-notes/\n')
        self.run_tool('--apply')
        self.assertEqual(before, (self.root / '.gitignore').read_bytes())
        self.assertEqual(b'# custom local rules\nprivate-notes/\n/.llm-wiki/\n', exclude.read_bytes())
        git(self.root, 'check-ignore', '.llm-wiki/Home.md')
        journal = json.loads(next((self.base / 'backups').glob('*/journal.json')).read_text())
        self.assertTrue(any(x['path'] == '.git/info/exclude' for x in journal['files']))

    def test_existing_exclusion_is_respected(self):
        exclude = self.root / '.git/info/exclude'
        exclude.write_bytes(b'.llm-wiki/\n')
        self.run_tool('--apply')
        self.assertEqual(b'.llm-wiki/\n', exclude.read_bytes())

    def test_ignore_negation_rolls_back_local_exclude_and_wiki(self):
        self.write('.gitignore', b'wiki/*.wiki/\n!/.llm-wiki/\n')
        exclude = self.root / '.git/info/exclude'
        before = exclude.read_bytes()
        snapshot = self.snapshot()
        with self.assertRaises(m.MigrationError):
            self.run_tool('--apply')
        self.assertEqual(before, exclude.read_bytes())
        self.assertEqual(snapshot, self.snapshot())
        self.assertFalse(exclude.with_name('exclude.lock').exists())

    def test_wiki_only_uses_local_exclude(self):
        before = (self.root / '.gitignore').read_bytes()
        self.run_tool('--wiki-only', '--apply')
        self.assertEqual(before, (self.root / '.gitignore').read_bytes())
        self.assertIn(b'/.llm-wiki/', (self.root / '.git/info/exclude').read_bytes())
        self.assertFalse(git(self.root, 'diff', '--name-only'))

    def test_tracked_existing_attachment_is_rejected(self):
        self.wiki.rename(self.root / '.llm-wiki')
        git(self.root, 'add', '-f', '.llm-wiki')
        git(self.root, 'commit', '-qm', 'Accidentally tracked nested wiki')
        with self.assertRaisesRegex(m.MigrationError, 'tracked by the parent'):
            self.run_tool('--wiki-only', '--apply')

    def test_other_legacy_wiki_remains_ignored(self):
        self.write('.gitignore', b'# no shared wiki exclusions\n')
        self.write('wiki/.gitignore', upstream('wiki/.gitignore', self.slug))
        shutil.copytree(self.wiki, self.root / 'wiki/other.wiki')
        self.run_tool('--wiki-slug', self.slug, '--apply')
        self.assertTrue((self.root / 'wiki/other.wiki/.git').is_dir())
        git(self.root, 'check-ignore', 'wiki/other.wiki/Home.md')

    def test_preview(self):
        before = self.snapshot()
        self.run_tool()
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.base / 'backups').exists())

    def test_generic_apply_preserves_custom_settings_and_wiki(self):
        state = [git(self.wiki, *args) for args in [('status', '--porcelain'), ('remote', '-v'), ('rev-parse', 'HEAD')]]
        self.run_tool('--apply')
        target = self.root / '.llm-wiki'
        self.assertEqual(state, [git(target, *args) for args in [('status', '--porcelain'), ('remote', '-v'), ('rev-parse', 'HEAD')]])
        self.assertEqual(b'untracked', (target / 'scratch').read_bytes())
        self.assertFalse((self.root / 'llm-wiki.md').exists())
        settings = json.loads((self.root / '.claude/settings.json').read_text())
        self.assertEqual(self.settings['permissions'], settings['permissions'])
        self.assertEqual([{'type': 'command', 'command': 'echo custom'}], settings['hooks']['SessionStart'][0]['hooks'])
        self.assertIn('Keep custom instructions.', (self.root / 'CLAUDE.md').read_text())
        self.assertIn('.llm-wiki/', (self.root / 'CLAUDE.md').read_text())
        git(self.root, 'check-ignore', '.llm-wiki/Home.md')
        self.assertFalse(git(self.root, 'diff', '--cached', '--name-only'))

    def test_rerun(self):
        self.run_tool('--apply')
        before = self.snapshot()
        self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())
        self.assertEqual(1, len(list((self.base / 'backups').iterdir())))

    def test_other_project_name(self):
        self.wiki.rename(self.root / 'wiki/astronomy.wiki')
        # Use an independent fixture identity throughout the generated files.
        for p in self.root.rglob('*'):
            if p.is_file() and '.git' not in p.relative_to(self.root).parts:
                p.write_bytes(p.read_bytes().replace(self.slug.encode(), b'astronomy'))
        self.run_tool('--apply')
        self.assertTrue((self.root / '.llm-wiki/Home.md').exists())

    def test_custom_template_file_blocks(self):
        self.write('llm-wiki.md', b'custom tooling')
        before = self.snapshot()
        with self.assertRaisesRegex(m.MigrationError, 'unrecognized'):
            self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())

    def test_unknown_files_preserved(self):
        self.write('scripts/my-science.py', b'custom')
        self.run_tool('--apply')
        self.assertEqual(b'custom', (self.root / 'scripts/my-science.py').read_bytes())

    def test_custom_hook_blocks(self):
        self.settings['hooks']['SessionStart'][0]['hooks'][0]['command'] += ' && echo custom'
        self.write('.claude/settings.json', json.dumps(self.settings).encode())
        with self.assertRaisesRegex(m.MigrationError, 'custom legacy hook'):
            self.run_tool('--apply')
        self.assertTrue(self.wiki.exists())

    def test_managed_snippet_removed(self):
        import re
        versions = CATALOG['files']['wiki/agents/claude-code/templates/claude-md-snippet.md']
        raw = next(base64.b64decode(value) for value in versions
                   if b'<!-- lw:wiki-maintenance -->' in base64.b64decode(value))
        raw = raw.replace(b'${REPO_NAME}', self.slug.encode()).replace(b'{{REPO_NAME}}', self.slug.encode())
        snippet = re.search(rb'<!-- lw:wiki-maintenance -->.*?<!-- /lw:wiki-maintenance -->', raw, re.S).group()
        self.write('CLAUDE.md', b'# Custom\nMy instructions\n' + snippet)
        self.run_tool('--apply')
        self.assertEqual(b'# Custom\nMy instructions\n', (self.root / 'CLAUDE.md').read_bytes())

    def test_custom_managed_snippet_blocks(self):
        self.write('CLAUDE.md', b'<!-- lw:wiki-maintenance -->\nCustom wiki/agents/verification-gate.md\n<!-- /lw:wiki-maintenance -->')
        self.write('wiki/agents/verification-gate.md', upstream('wiki/agents/verification-gate.md', self.slug))
        with self.assertRaisesRegex(m.MigrationError, 'retained instructions'):
            self.run_tool('--apply')

    def test_collision(self):
        shutil.copytree(self.wiki, self.root / '.llm-wiki')
        with self.assertRaisesRegex(m.MigrationError, 'Both'):
            self.run_tool('--apply')

    def test_multiple_wikis(self):
        shutil.copytree(self.wiki, self.root / 'wiki/other.wiki')
        with self.assertRaisesRegex(m.MigrationError, 'Multiple'):
            self.run_tool('--apply')

    def test_symlink(self):
        (self.root / 'llm-wiki.md').unlink()
        (self.root / 'llm-wiki.md').symlink_to(self.root / 'CLAUDE.md')
        with self.assertRaisesRegex(m.MigrationError, 'Symlink'):
            self.run_tool('--apply')

    def test_staged_parent(self):
        self.write('custom', b'work')
        git(self.root, 'add', 'custom')
        with self.assertRaisesRegex(m.MigrationError, 'staged'):
            self.run_tool('--apply')

    def test_rollback(self):
        before = self.snapshot()
        real = m.atomic_write
        failed = False
        def fail(path, content, mode):
            nonlocal failed
            if path.name == 'CLAUDE.md' and not failed:
                failed = True
                raise OSError('injected failure')
            return real(path, content, mode)
        with patch.object(m, 'atomic_write', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'injected'):
                self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())
        self.assertTrue(self.wiki.exists())

if __name__ == '__main__':
    unittest.main()
