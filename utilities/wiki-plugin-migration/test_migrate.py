import base64
import contextlib
import io
import json
from pathlib import Path
import shutil
import shlex
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

    def test_custom_feature_block_is_removed(self):
        self.install_feature_fixture()
        self.write('CLAUDE.md', b'<!-- feature:agent-comms -->\nMy custom routing policy\n<!-- /feature:agent-comms -->')
        self.run_tool('--apply')
        self.assertEqual(b'', (self.root / 'CLAUDE.md').read_bytes())

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

    def test_printed_staging_command_selects_only_migration_paths(self):
        self.write('unrelated-tracked.txt', b'original')
        git(self.root, 'add', 'unrelated-tracked.txt')
        git(self.root, 'commit', '-qm', 'unrelated file')
        self.write('unrelated-tracked.txt', b'new unrelated work')
        self.write('unrelated-new.txt', b'untracked work')
        with (self.root / '.gitignore').open('ab') as out:
            out.write(b'.claude/settings.local.json\n')
        self.write('.claude/settings.local.json', json.dumps(self.settings).encode())
        self.write('wiki/agents/verification-gate.md', upstream('wiki/agents/verification-gate.md', self.slug))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            m.main([str(self.root), '--apply', '--backup-dir', str(self.base / 'backup with spaces')])
        self.assertFalse(git(self.root, 'diff', '--cached', '--name-only'))
        command = next(line.strip() for line in output.getvalue().splitlines()
                       if line.strip().startswith('git --literal-pathspecs add'))
        subprocess.run(shlex.split(command), cwd=self.root, check=True)
        staged = set(git(self.root, 'diff', '--cached', '--name-only', '-z').split(b'\0')) - {b''}
        journal = json.loads(next((self.base / 'backup with spaces').glob('*/journal.json')).read_text())
        paths = set(Path(journal['staging_file']).read_bytes().split(b'\0')) - {b''}
        self.assertEqual(paths, staged)
        self.assertIn(b'CLAUDE.md', staged)
        self.assertIn(b'llm-wiki.md', staged)  # tracked deletion
        self.assertNotIn(b'unrelated-tracked.txt', staged)
        self.assertNotIn(b'unrelated-new.txt', staged)
        self.assertNotIn(b'.gitignore', staged)
        self.assertNotIn(b'.claude/settings.local.json', staged)
        self.assertNotIn(b'wiki/agents/verification-gate.md', staged)  # deleted untracked
        self.assertNotIn(b'.git/info/exclude', paths)
        self.assertNotIn(b'.llm-wiki', paths)
        self.assertIn('  git push', output.getvalue())

    def test_literal_staging_handles_special_names_and_new_files(self):
        name = 'odd [name]*\nfile.txt'
        self.write('odd n-other.txt', b'unrelated')
        changes = [(name, None, b'migration-created')] + m.local_exclude_plan(self.root.resolve())
        backup = m.apply(self.root.resolve(), changes, None, self.root / '.llm-wiki', self.base / 'backup')
        git(self.root, '--literal-pathspecs', 'add', '-A',
            '--pathspec-from-file=' + str(backup / 'migration-paths.nul'), '--pathspec-file-nul')
        self.assertEqual(name.encode() + b'\0', git(self.root, 'diff', '--cached', '--name-only', '-z'))

    def test_wiki_only_empty_staging_file_has_no_add_command(self):
        self.run_tool('--wiki-only', '--apply')
        backup = next((self.base / 'backups').iterdir())
        self.assertEqual(b'', (backup / 'migration-paths.nul').read_bytes())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            m.print_git_instructions(self.root, backup)
        self.assertNotIn('git --literal-pathspecs add', output.getvalue())
        self.assertNotIn('git commit', output.getvalue())

    def test_staging_manifest_failure_rolls_back(self):
        before = self.snapshot()
        with patch.object(m, 'staging_paths', side_effect=OSError('staging failure')):
            with self.assertRaisesRegex(OSError, 'staging failure'):
                self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list((self.base / 'backups').glob('*/migration-paths.nul')))

    def install_cleanup_residue(self):
        self.write('features/README.md', upstream('features/README.md', self.slug))
        self.write('features/.gitkeep', b'')
        self.write('docs/adding-a-feature.md', upstream('docs/adding-a-feature.md', self.slug))
        self.write('features/.DS_Store', b'mac metadata')
        (self.root / 'features/agent-comms/code').mkdir(parents=True)
        (self.root / 'features/agent-comms/ci').mkdir()
        self.write('wiki/agents/claude-code/.DS_Store', b'mac metadata')
        (self.root / 'wiki/agents/claude-code/templates').mkdir()
        self.write('wiki/.gitignore', upstream('wiki/.gitignore', self.slug))
        self.write('wiki/WIKI-INDEX.md', ('---\ntype: index\n---\n\n# Wiki Index — wiki\n\n## Wikis\n'
                   f'- [[Home_{self.slug}]] — Custom Project Title wiki\n').encode())
        self.write('.llm-wiki-template-log.md', b'## [2026-07-18] pulled template @55d94d9 - 1 file(s) updated\n- wiki/init-wiki.sh\n\n')
        self.write('.features-enabled', b'agent-comms\n')
        self.write('scripts/lib/common.sh', upstream('scripts/lib/common.sh', self.slug))

    def test_removes_generated_residue_and_empty_template_trees(self):
        self.install_cleanup_residue()
        self.run_tool('--apply')
        for name in ('wiki', 'scripts', 'features', 'docs', '.features-enabled', '.llm-wiki-template-log.md'):
            self.assertFalse((self.root / name).exists(), name)
        self.assertTrue((self.root / '.llm-wiki/Home.md').exists())
        self.run_tool('--apply')
        self.assertEqual(1, len(list((self.base / 'backups').iterdir())))

    def test_project_script_keeps_scripts_directory_and_is_reported(self):
        self.install_cleanup_residue()
        self.write('scripts/agent-msg/agents-send.sh', b'project messaging tool')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m.main([str(self.root), '--apply', '--backup-dir', str(self.base / 'backups')])
        self.assertIn('scripts/agent-msg/agents-send.sh', out.getvalue())
        self.assertEqual(b'project messaging tool', (self.root / 'scripts/agent-msg/agents-send.sh').read_bytes())
        self.assertFalse((self.root / 'scripts/lib').exists())

    def test_generated_index_preserves_other_wiki_entry(self):
        self.install_cleanup_residue()
        with (self.root / 'wiki/WIKI-INDEX.md').open('ab') as out:
            out.write(b'- [[Home_other]] \xe2\x80\x94 Other wiki\n')
        self.run_tool('--apply')
        index = (self.root / 'wiki/WIKI-INDEX.md').read_text()
        self.assertIn('Home_other', index)
        self.assertNotIn(f'Home_{self.slug}', index)

    def test_directory_removal_rolls_back_with_original_modes(self):
        self.install_cleanup_residue()
        (self.root / 'features/agent-comms').chmod(0o750)
        before = self.snapshot()
        dirs = {str(p.relative_to(self.root)): p.stat().st_mode & 0o777
                for p in self.root.rglob('*') if p.is_dir() and '.git' not in p.relative_to(self.root).parts}
        with patch.object(m, 'staging_paths', side_effect=OSError('after directory cleanup')):
            with self.assertRaisesRegex(OSError, 'after directory cleanup'):
                self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())
        for name, mode in dirs.items():
            self.assertTrue((self.root / name).is_dir(), name)
            self.assertEqual(mode, (self.root / name).stat().st_mode & 0o777)

    def test_custom_wiki_sections_and_commands_removed_project_guidance_retained(self):
        before = (f'# Project\nKeep research guidance.\n\n## Wiki\nCustom wiki/{self.slug}.wiki instructions.\n'
                  '### Knowledge Graph\nUse scripts/kg/build-graph.sh with our custom flags.\n'
                  '```sh\n# command example heading\nbash wiki/init-wiki.sh\n```\n'
                  '## Experiments\nKeep seed 42.\nRun scripts/kg/build-graph.sh after testing.\n'
                  '```sh\npytest tests/\n```\n'
                  '<!-- feature:other -->\nKeep other feature.\n<!-- /feature:other -->\n')
        self.write('CLAUDE.md', before.encode())
        self.write('AGENTS.md', before.encode())
        self.run_tool('--apply')
        for name in ('CLAUDE.md', 'AGENTS.md'):
            after = (self.root / name).read_text()
            self.assertIn('Keep research guidance.', after)
            self.assertIn('## Experiments\nKeep seed 42.', after)
            self.assertIn('```sh\npytest tests/\n```', after)
            self.assertIn('Keep other feature.', after)
            self.assertNotIn('## Wiki', after)
            self.assertNotIn('scripts/kg', after)
            self.assertNotIn('wiki/init-wiki.sh', after)
        backup = next((self.base / 'backups').iterdir())
        self.assertEqual(before.encode(), (backup / 'files/CLAUDE.md').read_bytes())

    def test_unrelated_graph_section_survives(self):
        self.write('CLAUDE.md', b'# Project\n## Knowledge Graph\nResearch graph experiments use src/graph.py.\n')
        self.run_tool('--apply')
        self.assertIn('Research graph experiments', (self.root / 'CLAUDE.md').read_text())

    def test_malformed_instruction_markers_fail_without_edits(self):
        self.write('CLAUDE.md', b'Project guidance\n<!-- lw:wiki-maintenance -->\nunterminated block')
        before = self.snapshot()
        with self.assertRaisesRegex(m.MigrationError, 'Unbalanced'):
            self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())

    def test_current_plugin_wiki_instructions_survive(self):
        content = b'# Project\n## Wiki\nUse .llm-wiki/ and the llm-wiki plugin.\n'
        self.write('CLAUDE.md', content)
        self.run_tool('--apply')
        self.assertEqual(content, (self.root / 'CLAUDE.md').read_bytes())

    def test_known_bytecode_and_desktop_clutter_removed_and_backed_up(self):
        self.write('scripts/kg/__pycache__/wiki-to-jsonld.cpython-313.pyc', b'compiled wiki/init-wiki.sh')
        self.write('scripts/kg/wiki-to-jsonld.pyo', b'old compiled')
        self.write('scripts/kg/Thumbs.db', b'thumbnails')
        self.write('scripts/kg/Desktop.ini', b'desktop settings')
        self.run_tool('--apply')
        self.assertFalse((self.root / 'scripts/kg').exists())
        backup = next((self.base / 'backups').iterdir())
        self.assertEqual(b'compiled wiki/init-wiki.sh', (backup / 'files/scripts/kg/__pycache__/wiki-to-jsonld.cpython-313.pyc').read_bytes())
        self.assertTrue((backup / 'leftovers.txt').is_file())

    def test_custom_cache_and_script_are_preserved_and_reported(self):
        self.write('scripts/kg/__pycache__/project_code.cpython-313.pyc', b'custom')
        self.write('scripts/kg/Thumbs.db', b'thumbnails')
        self.write('scripts/project.py', b'print("project")')
        (self.root / 'scripts/user-empty-folder').mkdir()
        self.run_tool('--apply')
        self.assertEqual(b'custom', (self.root / 'scripts/kg/__pycache__/project_code.cpython-313.pyc').read_bytes())
        self.assertTrue((self.root / 'scripts/kg/Thumbs.db').exists())
        report = next((self.base / 'backups').glob('*/leftovers.txt')).read_text()
        self.assertIn('clutter retained', report)
        self.assertIn('scripts/project.py [project/unknown file; preserved]', report)
        self.assertIn('user-empty-folder/ [empty directory', report)

    def test_cache_clutter_is_restored_on_failure(self):
        self.write('scripts/kg/__pycache__/wiki-to-jsonld.cpython-313.pyc', b'compiled')
        self.write('scripts/kg/Thumbs.db', b'thumbnails')
        before = self.snapshot()
        with patch.object(m, 'staging_paths', side_effect=OSError('after cache cleanup')):
            with self.assertRaisesRegex(OSError, 'after cache cleanup'):
                self.run_tool('--apply')
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list((self.base / 'backups').glob('*/leftovers.txt')))

    def test_cache_symlink_is_not_followed(self):
        outside = self.base / 'outside.pyc'
        outside.write_bytes(b'keep outside')
        cache = self.root / 'scripts/kg/__pycache__'
        cache.mkdir(parents=True)
        (cache / 'wiki-to-jsonld.cpython-313.pyc').symlink_to(outside)
        self.run_tool('--apply')
        self.assertTrue((cache / 'wiki-to-jsonld.cpython-313.pyc').is_symlink())
        self.assertEqual(b'keep outside', outside.read_bytes())
        self.assertIn('[symlink; preserved]', next((self.base / 'backups').glob('*/leftovers.txt')).read_text())

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

    def test_custom_managed_snippet_is_removed(self):
        self.write('CLAUDE.md', b'<!-- lw:wiki-maintenance -->\nCustom wiki/agents/verification-gate.md\n<!-- /lw:wiki-maintenance -->')
        self.write('wiki/agents/verification-gate.md', upstream('wiki/agents/verification-gate.md', self.slug))
        self.run_tool('--apply')
        self.assertEqual(b'', (self.root / 'CLAUDE.md').read_bytes())

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
