#!/usr/bin/env python3
"""Preview/apply template-to-plugin migration for an llm-wiki repository."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import shlex
import subprocess
import sys
import tempfile
from template_cleanup import cleanup, reference_catalog


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
        validate_checkout(target)
        return None, target, "already attached"
    if slug:
        raise MigrationError(f"No wiki found at wiki/{slug}.wiki or .llm-wiki")
    return None, target, "no local wiki; plugin initialization is a separate step"


def load_profile(path):
    data = json.loads(path.read_text())
    if data.get("version") != 1:
        raise MigrationError("Unsupported migration profile version")
    seen = set()
    for item in data["files"]:
        name = item["path"]
        if name in seen or name.split("/")[0] == ".llm-wiki":
            raise MigrationError(f"Duplicate or forbidden profile path: {name}")
        seen.add(name)
        if item["action"] not in ("create", "replace", "delete"):
            raise MigrationError(f"Invalid profile action: {name}")
        if item["action"] != "delete":
            content = base64.b64decode(item["after_base64"], validate=True)
            if hashlib.sha256(content).hexdigest() != item["after_sha256"]:
                raise MigrationError(f"Invalid profile checksum: {name}")
    return data


def file_plan(root, profile):
    changes, conflicts = [], []
    for item in profile["files"]:
        path = safe_path(root, item["path"])
        exists = present(path)
        if exists and not path.is_file():
            conflicts.append(f"{item['path']}: expected a regular file")
            continue
        content = path.read_bytes() if exists else None
        digest = hashlib.sha256(content).hexdigest() if exists else None
        after = None if item["action"] == "delete" else base64.b64decode(item["after_base64"])
        if content == after:
            continue
        if digest != item.get("before_sha256"):
            conflicts.append(f"{item['path']}: differs from both audited original and migrated version")
            continue
        changes.append((item["path"], content, after))
    if conflicts:
        raise MigrationError("Customized/missing files require manual review:\n  " + "\n  ".join(conflicts))
    return changes


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


def apply(root, changes, source, target, backup_parent):
    # Backups include the original bytes and modes; the wiki itself is renamed intact.
    backup_parent.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="wiki-migration-", dir=backup_parent))
    os.chmod(backup, 0o700)
    records = []
    for name, before, after in changes:
        path = root / name
        mode = path.stat().st_mode & 0o777 if before is not None else 0o644
        records.append({"path": name, "existed": before is not None, "mode": mode})
        if before is not None:
            dest = backup / "files" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(before)
    journal = {"repo": str(root), "wiki_from": str(source) if source else None,
               "wiki_to": str(target), "files": records, "state": "prepared"}
    journal_path = backup / "journal.json"
    journal_path.write_text(json.dumps(journal, indent=2) + "\n")
    print(f"Backup and recovery journal: {backup}", flush=True)
    completed, moved = [], False
    try:
        # Recheck all inputs after backing up, before the first mutation.
        for name, before, after in changes:
            path = safe_path(root, name)
            current = path.read_bytes() if present(path) else None
            if current != before:
                raise MigrationError(f"File changed during migration: {name}")
        if source:
            if present(target):
                raise MigrationError(".llm-wiki appeared during migration")
            source.rename(target)
            moved = True
        for (name, before, after), record in zip(changes, records):
            path = root / name
            if after is None:
                path.unlink()
            else:
                atomic_write(path, after, record["mode"])
            completed.append((name, before, record["mode"]))
        if moved:
            validate_checkout(target)
        journal["state"] = "complete"
        journal_path.write_text(json.dumps(journal, indent=2) + "\n")
    except BaseException:
        for name, before, mode in reversed(completed):
            path = root / name
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


def print_git_instructions(root):
    print("\nTo review, stage, and commit the updated parent repository:")
    print("  cd " + shlex.quote(str(root)))
    print("  git status --short")
    print("  git diff")
    print("  git add -A")
    print("  git diff --cached")
    print('  git commit -m "Migrate template wiki tooling to plugin"')
    print("git add -A also stages unrelated changes; review first and use explicit paths if needed.")
    print("The ignored .llm-wiki checkout is a separate repository and is not staged here.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, help="repository root to migrate")
    parser.add_argument("--apply", action="store_true", help="apply the validated plan (default: preview only)")
    parser.add_argument("--wiki-slug", help="select wiki/<slug>.wiki if there are multiple")
    parser.add_argument("--wiki-only", action="store_true", help="only relocate wiki and add .llm-wiki to .gitignore")
    parser.add_argument("--profile", type=Path, help="explicit legacy exact-file profile (optional)")
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
    if args.wiki_only:
        changes = []
        if source or present(target):
            ignore = safe_path(root, ".gitignore")
            before = ignore.read_bytes() if ignore.exists() else None
            text = (before or b"").decode("utf-8")
            if not any(line.strip() in (".llm-wiki/", "/.llm-wiki/", ".llm-wiki", "/.llm-wiki") for line in text.splitlines()):
                after = (text + ("\n" if text and not text.endswith("\n") else "") + "\n# Local wiki checkout\n/.llm-wiki/\n").encode()
                changes.append((".gitignore", before, after))
    else:
        if args.profile:
            profile = load_profile(args.profile)
            changes = file_plan(root, profile)
            print(f"Explicit profile: {profile['name']}")
        else:
            catalog = json.loads(Path(__file__).with_name("template-catalog.json").read_text())
            if args.template_repo:
                extra = reference_catalog(args.template_repo)
                for name, versions in extra['files'].items():
                    catalog['files'].setdefault(name, []).extend(versions)
            changes = cleanup(root, source, target, args.wiki_slug, catalog, safe_path, MigrationError)
            print("Migration: upstream template to llm-wiki plugin")
    deletes = sum(after is None for _, _, after in changes)
    print(f"Repository: {root}\nWiki: {wiki_state}")
    if source:
        print(f"  {source} -> {target}")
    print(f"Files: {deletes} deletions, {len(changes) - deletes} writes")
    if args.verbose:
        for name, before, after in changes:
            print(f"  {'DELETE' if after is None else 'WRITE '} {name}")
    if not changes and not source:
        print("No changes needed.")
        return 0
    if not args.apply:
        print("Preview only. Run again with --apply to perform this plan.")
        return 0
    backup_parent = args.backup_dir.expanduser().resolve()
    if backup_parent == root or root in backup_parent.parents:
        raise MigrationError("Choose a backup directory outside the target repository")
    apply(root, changes, source, target, backup_parent)
    print("Migration complete. Changes are unstaged; review git diff before committing.")
    print("Plugins must be installed separately. No commits, pushes, or network operations were performed.")
    print("Use the plugin wiki-ask/wiki-enroll skills for agent communication; legacy /ask is retired.")
    print("If agent-comms becomes a separate plugin, enable only one provider of ask/enroll.")
    print_git_instructions(root)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MigrationError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
