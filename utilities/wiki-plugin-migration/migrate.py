#!/usr/bin/env python3
"""Preview/apply template-to-plugin migration for an llm-wiki repository."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import shlex
import subprocess
import sys
import tempfile
from template_cleanup import cleanup, reference_catalog
import retired_directories


class MigrationError(Exception):
    pass


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise MigrationError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def present(path):
    return path.exists() or path.is_symlink()


def safe_path(root, relative):
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts or ".git" in parts:
        raise MigrationError(f"Unsafe path: {relative}")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise MigrationError(f"Symlink requires manual review: {path}")
    return path


def validate_checkout(path):
    if not (path / ".git").is_dir() or (path / ".git").is_symlink():
        raise MigrationError(f"Expected standalone Git checkout with its own .git directory: {path}")
    if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise MigrationError(f"Not a Git repository root: {path}")
    # Linked worktrees and externally configured object stores need Git-aware relocation.
    if (path / ".git/worktrees").exists() or (path / ".git/objects/info/alternates").exists():
        raise MigrationError(f"Wiki has linked worktrees or alternate object storage: {path}")
    result = subprocess.run(["git", "-C", str(path), "config", "--get", "core.worktree"], capture_output=True)
    if result.returncode != 1:
        raise MigrationError(f"Wiki has core.worktree or unreadable configuration: {path}")
    git(path, "rev-parse", "--verify", "HEAD")


def wiki_plan(root, slug):
    wiki_dir = safe_path(root, "wiki")
    target = safe_path(root, ".llm-wiki")
    candidates = sorted(wiki_dir.glob("*.wiki")) if wiki_dir.is_dir() else []
    if slug:
        if slug in (".", "..") or "/" in slug or "\\" in slug:
            raise MigrationError("--wiki-slug must be a single repository name")
        source = safe_path(root, f"wiki/{slug}.wiki")
        candidates = [source] if present(source) else []
    if len(candidates) > 1:
        raise MigrationError("Multiple legacy wikis found; select one with --wiki-slug")
    source = safe_path(root, str(candidates[0].relative_to(root))) if candidates else None
    if source is not None and present(target):
        raise MigrationError("Both a legacy wiki and .llm-wiki exist; reconcile them manually. Nothing will be overwritten.")
    if source is not None:
        validate_checkout(source)
        if git(root, "ls-files", "--", str(source.relative_to(root))):
            raise MigrationError("Legacy wiki is tracked by the parent repository (or is a submodule)")
        return source, target, "move"
    if present(target):
        if git(root, 'ls-files', '--', '.llm-wiki'):
            raise MigrationError('.llm-wiki is tracked by the parent repository; reconcile it before migration')
        validate_checkout(target)
        return None, target, "already attached"
    if slug:
        raise MigrationError(f"No wiki found at wiki/{slug}.wiki or .llm-wiki")
    return None, target, "no local wiki; plugin initialization is a separate step"


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".wiki-migrate-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def planned_path(root, name):
    if name != '.git/info/exclude':
        return safe_path(root, name)
    path = Path(git(root, 'rev-parse', '--git-path', 'info/exclude'))
    if not path.is_absolute():
        path = root / path
    for part in (path, *path.parents):
        if part.is_symlink():
            raise MigrationError(f'Symlink requires manual review: {part}')
    return path


def local_exclude_plan(root):
    result = subprocess.run(['git', '-C', str(root), 'check-ignore', '--no-index',
                             '--quiet', '.llm-wiki/'], capture_output=True)
    if result.returncode == 0:
        return []
    if result.returncode != 1:
        raise MigrationError('Git could not check the wiki ignore rule')
    path = planned_path(root, '.git/info/exclude')
    if path.with_name(path.name + '.lock').exists():
        raise MigrationError('Git info/exclude is locked')
    before = path.read_bytes() if path.exists() else None
    content = before or b''
    if b'/.llm-wiki/' in content.splitlines():
        raise MigrationError('Existing local wiki exclusion is overridden; review Git ignore configuration')
    newline = b'\r\n' if b'\r\n' in content else b'\n'
    after = content + (newline if content and not content.endswith(b'\n') else b'') + b'/.llm-wiki/' + newline
    return [('.git/info/exclude', before, after)]


def apply(root, changes, source, target, backup_parent, directories=()):
    lock = None
    if any(name == '.git/info/exclude' for name, _, _ in changes):
        path = planned_path(root, '.git/info/exclude')
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = path.with_name(path.name + '.lock')
        with candidate.open('xb'):
            pass
        lock = candidate
    try:
        return apply_transaction(root, changes, source, target, backup_parent, directories)
    finally:
        if lock is not None:
            lock.unlink()


def staging_paths(root, changes):
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                            capture_output=True, check=True)
    tracked = set(result.stdout.split(b'\0'))
    paths = []
    for name, before, after in changes:
        if PurePosixPath(name).parts[0] in {'.git', '.llm-wiki'}:
            continue
        encoded = os.fsencode(name)
        if encoded in tracked:
            paths.append(encoded)
        elif after is not None:
            ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '--quiet', '--', name],
                                     capture_output=True)
            if ignored.returncode == 1:
                paths.append(encoded)
            elif ignored.returncode != 0:
                raise MigrationError(f'Cannot check staging eligibility: {name}')
        # Already-deleted untracked files have nothing to stage.
    return sorted(set(paths))


def apply_transaction(root, changes, source, target, backup_parent, directories=()):
    # Backups include the original bytes and modes; the wiki itself is renamed intact.
    backup_parent.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="wiki-migration-", dir=backup_parent))
    os.chmod(backup, 0o700)
    records = []
    for name, before, after in changes:
        path = planned_path(root, name)
        mode = path.stat().st_mode & 0o777 if before is not None else 0o644
        records.append({"path": name, "existed": before is not None, "mode": mode, "resolved_path": str(path)})
        if before is not None:
            dest = backup / "files" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(before)
    journal = {"repo": str(root), "wiki_from": str(source) if source else None,
               "wiki_to": str(target), "files": records, "directories": list(directories), "state": "prepared"}
    journal_path = backup / "journal.json"
    journal_path.write_text(json.dumps(journal, indent=2) + "\n")
    print(f"Backup and recovery journal: {backup}", flush=True)
    pathspec = backup / "migration-paths.nul"
    completed, moved = [], False
    removed_dirs = []
    try:
        # Recheck all inputs after backing up, before the first mutation.
        for name, before, after in changes:
            path = planned_path(root, name)
            current = path.read_bytes() if present(path) else None
            if current != before:
                raise MigrationError(f"File changed during migration: {name}")
        if source:
            if present(target):
                raise MigrationError(".llm-wiki appeared during migration")
            source.rename(target)
            moved = True
        for (name, before, after), record in zip(changes, records):
            path = planned_path(root, name)
            if after is None:
                path.unlink()
            else:
                atomic_write(path, after, record["mode"])
            completed.append((name, before, record["mode"]))
        for entry in directories:
            safe_path(root, entry['path']).rmdir()
            removed_dirs.append(entry)
        git(root, 'check-ignore', '--no-index', '--quiet', '.llm-wiki/')
        if moved:
            validate_checkout(target)
        paths = staging_paths(root, changes)
        pathspec.write_bytes(b''.join(path + b'\0' for path in paths))
        journal['staging_file'] = str(pathspec)
        journal['staging_path_count'] = len(paths)
        leftovers = retired_directories.remaining_files(root)
        (backup / 'leftovers.txt').write_text('\n'.join(leftovers) + ('\n' if leftovers else 'No leftovers in inspected template locations.\n'))
        journal['leftover_report'] = str(backup / 'leftovers.txt')
        journal["state"] = "complete"
        journal_path.write_text(json.dumps(journal, indent=2) + "\n")
    except BaseException:
        pathspec.unlink(missing_ok=True)
        (backup / 'leftovers.txt').unlink(missing_ok=True)
        for entry in reversed(removed_dirs):
            path = safe_path(root, entry["path"])
            path.mkdir()
            path.chmod(entry["mode"])
        for name, before, mode in reversed(completed):
            path = planned_path(root, name)
            if before is None:
                path.unlink()
            else:
                atomic_write(path, before, mode)
        if moved:
            target.rename(source)
        journal["state"] = "rolled back"
        journal_path.write_text(json.dumps(journal, indent=2) + "\n")
        raise
    return backup


def print_git_instructions(root, backup):
    pathspec = backup / 'migration-paths.nul'
    print('\nMigration staging file: ' + str(pathspec))
    if not pathspec.read_bytes():
        print('No parent-repository files from this migration need staging.')
        return
    print("To review, stage, commit, and push the migration changes:")
    print("  cd " + shlex.quote(str(root)))
    print("  git status --short")
    print("  git diff")
    print("  git --literal-pathspecs add -A --pathspec-from-file=" + shlex.quote(str(pathspec)) + " --pathspec-file-nul")
    print("  git diff --cached")
    print('  git commit -m "Migrate template wiki tooling to plugin"')
    print('  git push')
    print('Only migration paths are selected; existing edits within those files are included, so review the staged diff.')
    print('Git-local metadata, ignored untracked files, and the separate wiki are excluded.')


def print_attachment_guidance(wiki):
    if not wiki.is_dir():
        print('No wiki is attached. Use wiki-init and choose GitHub or offline storage explicitly.')
        return
    schemas = sorted(wiki.glob('SCHEMA_*.md'))
    if len(schemas) == 1:
        name = schemas[0].stem[len('SCHEMA_'):]
        print('Preserved wiki namespace: ' + name)
        print('Existing schema is retained; wiki-init does not upgrade its contents.')
        print('If wiki-init is needed, preserve the namespace with --repo-name ' + shlex.quote(name) + '.')
        for filename in (f'index_{name}.md', f'log_{name}.md'):
            if not (wiki / filename).is_file():
                print('Review missing navigation file: .llm-wiki/' + filename)
    elif (wiki / 'SCHEMA.md').is_file():
        print('Bare SCHEMA.md is accepted by init, but startup expects namespaced navigation/schema; review those files.')
    else:
        print('Review missing or ambiguous wiki schema/namespace before using wiki-init; initialization can create and commit pages.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, help="repository root to migrate")
    parser.add_argument("--apply", action="store_true", help="apply the validated plan (default: preview only)")
    parser.add_argument("--wiki-slug", help="select wiki/<slug>.wiki if there are multiple")
    parser.add_argument("--wiki-only", action="store_true", help="only relocate wiki and ensure a local Git exclusion")
    parser.add_argument("--template-repo", type=Path, help="additional upstream template checkout for revision matching")
    parser.add_argument("--backup-dir", type=Path, default=Path.home() / ".local/share/llm-wiki/migration-backups")
    parser.add_argument("--verbose", action="store_true", help="list each file action")
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise MigrationError("Pass the repository root, not a subdirectory")
    if git(root, "diff", "--cached", "--name-only"):
        raise MigrationError("The parent repository has staged changes; commit or unstage them before migration")
    source, target, wiki_state = wiki_plan(root, args.wiki_slug)
    catalog = None
    directories = []
    if args.wiki_only:
        changes = []

    else:
        catalog = json.loads(Path(__file__).with_name("template-catalog.json").read_text())
        if args.template_repo:
            extra = reference_catalog(args.template_repo)
            for name, versions in extra['files'].items():
                catalog['files'].setdefault(name, []).extend(versions)
        changes = cleanup(root, source, target, args.wiki_slug, catalog, safe_path, MigrationError)
        print("Migration: upstream template to llm-wiki plugin")
    # Local exclusions apply to every migration mode; shared .gitignore stays untouched.
    changes = [change for change in changes if change[0] not in {".gitignore", "wiki/.gitignore"}]
    if catalog is not None:
        changes, directories = retired_directories.plan(root, changes, source, catalog, safe_path)
    changes.extend(local_exclude_plan(root))
    deletes = sum(after is None for _, _, after in changes)
    print(f"Repository: {root}\nWiki: {wiki_state}")
    if source:
        print(f"  {source} -> {target}")
    print(f"Files: {deletes} deletions, {len(changes) - deletes} writes")
    if args.verbose:
        for name, before, after in changes:
            print(f"  {'DELETE' if after is None else 'WRITE '} {name}")
    if directories:
        print(f"Empty template directories to remove: {len(directories)}")
        if args.verbose:
            for entry in directories:
                print("  RMDIR " + entry["path"])
    remaining = retired_directories.remaining_files(root, changes, source, directories)
    if remaining:
        print('Predicted leftovers in template locations (preserved for review):')
        for name in remaining:
            print('  ' + name)
    if not changes and not source and not directories:
        print("No changes needed.")
        print_attachment_guidance(target)
        return 0
    if not args.apply:
        print("Preview only. Run again with --apply to perform this plan.")
        return 0
    backup_parent = args.backup_dir.expanduser().resolve()
    if backup_parent == root or root in backup_parent.parents:
        raise MigrationError("Choose a backup directory outside the target repository")
    backup = apply(root, changes, source, target, backup_parent, directories)
    print("Migration complete. Changes are unstaged; review git diff before committing.")
    print("Plugins must be installed separately. No commits, pushes, or network operations were performed.")
    print("Use the plugin wiki-ask/wiki-enroll skills for agent communication; legacy /ask is retired.")
    print("If agent-comms becomes a separate plugin, enable only one provider of ask/enroll.")
    report = backup / 'leftovers.txt'
    print('Post-migration leftover report: ' + str(report))
    print(report.read_text().rstrip())
    print_attachment_guidance(target)
    print_git_instructions(root, backup)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MigrationError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
