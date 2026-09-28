import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("migration", HERE / "migrate.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def init(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Migration Test")
    git(root, "config", "user.email", "migration@example.invalid")


@unittest.skipUnless(os.environ.get("MIGRATION_SOURCE_REPO"), "optional historical profile fixture")
class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.TemporaryDirectory(prefix="wiki-migration-tests-")
        cls.fixture = Path(cls.base.name) / "template"
        init(cls.fixture)
        # A real source revision, supplied only to build the integration fixture.
        source = os.environ["MIGRATION_SOURCE_REPO"]
        archive = git(source, "archive", "c39d65a^")
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # Trusted local Git archive, not an uploaded tarball; supports Python 3.9.
            tar.extractall(cls.fixture)
        git(cls.fixture, "add", ".")
        git(cls.fixture, "commit", "-qm", "original template")

    @classmethod
    def tearDownClass(cls):
        cls.base.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wiki-migration-case-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo with spaces"
        shutil.copytree(self.fixture, self.root)
        self.backup = Path(self.tmp.name) / "backups"
        self.wiki = self.root / "wiki/example.wiki"
        init(self.wiki)
        (self.wiki / "Home.md").write_text("wiki memory\n")
        git(self.wiki, "add", ".")
        git(self.wiki, "commit", "-qm", "wiki initial")
        git(self.wiki, "remote", "add", "origin", "https://example.invalid/example.wiki.git")
        (self.wiki / "Home.md").write_text("uncommitted wiki work\n")
        (self.wiki / "draft.md").write_text("staged draft\n")
        git(self.wiki, "add", "draft.md")
        (self.wiki / "scratch.txt").write_text("untracked\n")

    def run_tool(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return migration.main([str(self.root), "--backup-dir", str(self.backup), "--profile", str(HERE / "naval-sensor-fusion.json"), *args])

    def snapshot(self, path):
        return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mode & 0o777)
                for p in path.rglob("*") if p.is_file() and ".git" not in p.relative_to(path).parts}

    def test_preview_is_read_only(self):
        before = self.snapshot(self.root)
        self.run_tool()
        self.assertEqual(before, self.snapshot(self.root))
        self.assertFalse(self.backup.exists())

    def test_full_migration_matches_commit_and_preserves_dirty_wiki(self):
        wiki_content = self.snapshot(self.wiki)
        head = git(self.wiki, "rev-parse", "HEAD")
        status = git(self.wiki, "status", "--porcelain")
        remote = git(self.wiki, "remote", "-v")
        app_head = git(self.root, "rev-parse", "HEAD")
        self.run_tool("--apply")
        target = self.root / ".llm-wiki"
        self.assertEqual(wiki_content, self.snapshot(target))
        self.assertEqual(head, git(target, "rev-parse", "HEAD"))
        self.assertEqual(status, git(target, "status", "--porcelain"))
        self.assertEqual(remote, git(target, "remote", "-v"))
        self.assertEqual(app_head, git(self.root, "rev-parse", "HEAD"))
        self.assertFalse(git(self.root, "diff", "--cached", "--name-only"))
        profile = migration.load_profile(HERE / "naval-sensor-fusion.json")
        profile["files"] = [item for item in profile["files"] if item["path"] not in {".gitignore", "wiki/.gitignore"}]
        self.assertEqual([], migration.file_plan(self.root, profile))
        self.assertFalse(self.wiki.exists())
        self.assertTrue(list(self.backup.glob("*/journal.json")))
        git(self.root, "check-ignore", ".llm-wiki/Home.md")

    def test_rerun_is_noop(self):
        self.run_tool("--apply")
        before = self.snapshot(self.root)
        count = len(list(self.backup.iterdir()))
        self.run_tool("--apply")
        self.assertEqual(before, self.snapshot(self.root))
        self.assertEqual(count, len(list(self.backup.iterdir())))

    def test_customized_file_blocks_everything(self):
        with (self.root / "CLAUDE.md").open("a") as out:
            out.write("\nCustom instructions to preserve\n")
        before = self.snapshot(self.root)
        with self.assertRaisesRegex(migration.MigrationError, "CLAUDE.md"):
            self.run_tool("--apply")
        self.assertEqual(before, self.snapshot(self.root))

    def test_unknown_files_preserved(self):
        custom = self.root / "scripts/custom-tool.py"
        custom.write_text("print('keep me')\n")
        self.run_tool("--apply")
        self.assertTrue(custom.exists())

    def test_both_wikis_block(self):
        shutil.copytree(self.wiki, self.root / ".llm-wiki")
        with self.assertRaisesRegex(migration.MigrationError, "Both"):
            self.run_tool("--apply")
        self.assertTrue((self.root / ".claude/settings.json").exists())

    def test_multiple_wikis_require_selection(self):
        shutil.copytree(self.wiki, self.root / "wiki/other.wiki")
        with self.assertRaisesRegex(migration.MigrationError, "Multiple"):
            self.run_tool()
        self.run_tool("--wiki-only", "--wiki-slug", "example", "--apply")
        self.assertTrue((self.root / "wiki/other.wiki/.git").exists())

    def test_symlink_target_blocks(self):
        (self.root / ".llm-wiki").symlink_to(self.wiki)
        with self.assertRaisesRegex(migration.MigrationError, "Symlink"):
            self.run_tool("--apply")

    def test_symlink_parent_blocks(self):
        shutil.move(self.root / "scripts", Path(self.tmp.name) / "external-scripts")
        (self.root / "scripts").symlink_to(Path(self.tmp.name) / "external-scripts")
        with self.assertRaisesRegex(migration.MigrationError, "Symlink"):
            self.run_tool("--apply")

    def test_gitfile_wiki_blocks(self):
        shutil.move(self.wiki / ".git", Path(self.tmp.name) / "wiki-git")
        (self.wiki / ".git").write_text(f"gitdir: {self.tmp.name}/wiki-git\n")
        with self.assertRaisesRegex(migration.MigrationError, "standalone"):
            self.run_tool("--apply")

    def test_staged_main_change_blocks(self):
        (self.root / "custom.txt").write_text("staged")
        git(self.root, "add", "custom.txt")
        with self.assertRaisesRegex(migration.MigrationError, "staged"):
            self.run_tool("--apply")

    def test_wiki_only_preserves_template(self):
        before = (self.root / "CLAUDE.md").read_bytes()
        self.run_tool("--wiki-only", "--apply")
        self.assertEqual(before, (self.root / "CLAUDE.md").read_bytes())
        self.assertTrue((self.root / ".llm-wiki/.git").is_dir())

    def test_failure_rolls_back_files_and_move(self):
        before = self.snapshot(self.root)
        real_write = migration.atomic_write
        failed = False

        def fail_once(path, data, mode):
            nonlocal failed
            if path.name == "CLAUDE.md" and not failed:
                failed = True
                raise OSError("simulated disk failure")
            return real_write(path, data, mode)

        with patch.object(migration, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.run_tool("--apply")
        self.assertEqual(before, self.snapshot(self.root))
        self.assertTrue(self.wiki.exists())
        journal = json.loads(next(self.backup.glob("*/journal.json")).read_text())
        self.assertEqual("rolled back", journal["state"])


if __name__ == "__main__":
    unittest.main()
