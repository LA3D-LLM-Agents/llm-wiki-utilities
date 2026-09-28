# General llm-wiki template → plugin migration

Python 3.9+ and Git. No Python packages or network access are needed at runtime.
Keep this folder outside the target repository.

The default migrates repositories derived from
https://github.com/crcresearch/llm-wiki-memory-template to the `.llm-wiki/`
layout used by https://github.com/LA3D-LLM-Agents/llm-wiki-colab.
There is no project-specific default, required project name, or cleanup commit.

```sh
python3 migrate.py /path/to/any-project --verbose       # read-only preview
python3 migrate.py /path/to/any-project --apply         # apply reviewed plan
```

## What it does

- Detects `wiki/*.wiki` independently of the parent checkout's name. Use
  `--wiki-slug my-project` to select among multiple wikis or supply a legacy
  identity when no local wiki is present.
- Moves the entire standalone wiki checkout to `.llm-wiki`, preserving Git
  history, remotes, staged changes, unstaged changes and untracked files.
- Deletes only tooling files whose bytes match a bundled upstream template
  version, including supported repository-name placeholder substitutions.
  It does not delete directories wholesale or infer ownership from names alone.
- Removes exact recognized wiki hook commands from `.claude/settings.json` and
  `.claude/settings.local.json`, preserving custom hooks, matcher metadata,
  custom permissions, environment and other settings. It also removes exact
  upstream `permissions.allow` entries dedicated to legacy wiki paths, init,
  graph tooling and overlay setup. Generic Python permissions and user
  allow/deny/ask entries are preserved. Custom legacy permissions are flagged
  for review rather than silently removed or broadened.
- Cleans legacy wiki instructions from `CLAUDE.md` and `AGENTS.md`, including
  customized wording inside recognized template blocks. Preserves unrelated
  project guidance and current plugin instructions; backs up the original files.
- Ensures `.llm-wiki/` is excluded locally through Git's `info/exclude`,
  resolved using `git rev-parse --git-path info/exclude`, matching the plugin.
  Respects existing effective exclusions and leaves shared root `.gitignore` unchanged. A stock `wiki/.gitignore` is
  retired only when the legacy wiki directory can be removed completely; it
  remains when another wiki or project file still needs that directory.
- Checks retained active configuration, instructions, scripts and CI workflows
  for references to removed tooling or the old wiki path, stopping for review.

The script never commits, pushes, installs a plugin or updates wiki page contents.
Apply leaves the parent changes unstaged. After success, it saves
`migration-paths.nul` beside the backup journal and prints commands to review,
stage those exact paths, commit, and push:

```sh
git status --short
git diff
git --literal-pathspecs add -A --pathspec-from-file=/path/to/backup/migration-paths.nul --pathspec-file-nul
git diff --cached
git commit -m "Migrate template wiki tooling to plugin"
git push
```

The actual output includes a quoted `cd` and the exact staging-file path. The
NUL-delimited list handles spaces, newlines and wildcard characters literally.
It includes tracked migration deletions and eligible created/modified files;
it excludes unrelated files, Git-local metadata, the separate wiki, ignored
untracked files, and untracked files already deleted by migration. If nothing
needs staging, an empty list is saved and no add/commit/push command is printed.
Commands are printed only, never executed by migration. Review before running:
existing or subsequent edits within listed files will also be staged, and a
commit includes anything else you may already have staged. Use this list before
making further changes to the listed paths. Failed/rolled-back migrations do
not leave an actionable staging file. Previews do not create one. Install the appropriate plugin adapter
separately following the plugin repository's instructions, then start a new agent
session. Close agents using the target checkout while applying.

## Template versions and customizations

`template-catalog.json` contains exact committed upstream bytes from the revisions
listed in its `commits` field. The current catalog includes all 144 commits
reachable from upstream HEAD `49f0fc3`, including the historical template sync
revisions `55d94d9`, `67696a5` and `879c413`. This is coverage of upstream bytes,
not a claim that every generated or customized downstream file is recognized. The default is independent of any downstream
project. A checkout can contain files from multiple recognized template versions.
Missing optional overlays are fine.

For another template revision, supply an upstream checkout at that revision:

```sh
python3 migrate.py /path/to/project --template-repo /path/to/upstream-template
```

This adds that checkout's **committed HEAD** to the comparison catalog; it does
not execute its scripts or trust uncommitted changes. Supply the upstream
checkout, not a customized downstream project. Maintainers can rebuild the
bundled catalog with
`python3 build_catalog.py --all-history UPSTREAM_CHECKOUT ...`. Without
`--all-history`, only the supplied checkouts' committed HEAD revisions are read.
Only use genuine upstream template checkouts: a downstream instance used as a
reference can incorrectly authorize deleting its project-specific files.

A customized file at a recognized template-owned path blocks the entire migration
before writes. Customized hook commands and unresolved active references also
block. Review those files and migrate their custom behavior explicitly; the tool
does not silently discard it. Template layouts outside the catalog may need a
matching upstream checkout and manual resolution. “General” means the algorithm
and inputs work across projects, not that every arbitrary customization can be
safely migrated without review.

Project README files, research files, unknown files, and optional feature payloads
outside the recognized tooling inventory are preserved (agent-comms is now
explicitly recognized as described below). Historical documentation
may retain old paths. Personal memory outside the repository is not edited; review
any old per-user memory seed that directs agents to the previous wiki location.
Cursor rules are cleaned up when recognized, but the tool does not promise a
Cursor plugin adapter is available.

## Safety and recovery

Preview is read-only. Apply backs up changed file bytes/modes and a `journal.json`
outside the repository, by default under
`~/.local/share/llm-wiki/migration-backups/wiki-migration-*`.
Use `--backup-dir /path/outside/project` to override. The wiki itself is renamed
intact. Ordinary exceptions and Ctrl-C trigger rollback. A forced kill or disk
failure may require restoring files from the printed journal and renaming the
wiki back; do not use `git reset` or `git clean` to recover unrelated work.

The tool refuses parent staged changes, source/destination collisions, symlinked
paths, submodules, linked worktrees and external Git object stores. It is safe to
rerun after a successful migration; an immediate repeat makes no changes.

`--wiki-only` remains an explicitly partial operation: it only moves the wiki and
ensures its local Git exclusion. It does not clean up hooks or instructions.

## Tests

```sh
python3 -m unittest discover -s . -v
```

Tests use synthetic disposable repositories and the public upstream template catalog. They cover multiple identities, mixed custom
settings, managed prose, dirty wiki preservation, preview, repeat runs, collisions,
custom-file protection, symlinks, staged parent work and injected-failure rollback.
Optional plugin compatibility checks use a separately supplied upstream plugin checkout.

## Agent communication: one active provider

The llm-wiki plugin currently provides `wiki-ask` and `wiki-enroll`. The legacy
agent-comms feature is therefore included in migration, not kept as a second
implementation. The catalog maps genuine upstream feature payloads to their
installed paths: `scripts/agent-comms/`, `.github/workflows/agent-comms.yml`,
`.claude/rules/feature-agent-comms.md`, and the upstream `.claude/commands/ask.md`.
Exact upstream feature sections in CLAUDE.md/AGENTS.md are removed too, and only
the `agent-comms` line is removed from `.features-enabled`. Unchanged feature
source files under `features/agent-comms/` are retired with the old installer.

Custom/unknown executable payloads stop migration for review. Recognized legacy
agent-comms instruction blocks are removed even when their wording was customized. Other features are
preserved. The tool does not run the old feature-disable script, whose recursive
deletions would bypass the byte-match protection. Federation cards, wiki contents,
remotes and GitHub registration are not changed; there is no re-enrollment.

Use the plugin's wiki-ask/wiki-enroll skills after migration. The old `/ask`
command is retired rather than left shadowing a provider. If these capabilities
move into a separate agent-comms plugin, use one active provider of ask/enroll:
coordinate their removal/disablement in llm-wiki with enabling the new provider.
This tool does not install/uninstall plugins, inspect every user's enabled
plugins, or claim the proposed separate plugin already exists. Plugin startup
and command resolution still require verification in a fresh agent session.

Local exclusion changes are backed up and rolled back with the migration. The
journal records the resolved metadata path (which can differ in worktrees).
An exclusive `info/exclude.lock` protects the update. Git verifies the effective
ignore rule before success; a higher-priority negation causes rollback. Existing
shared ignore rules, including rules from earlier migrations, are preserved.

## Compatibility audit

See [AUDIT.md](AUDIT.md) for reproduced plugin init issues, migration fixes and
the actual-plugin test command. An existing schema is preserved, not upgraded.
After a rename, use its original namespace with `--repo-name` if running init;
the migration prints this reminder. Startup and init currently infer namespaces
differently. Review missing schema/index/log files before relying on the memory
snapshot. An already-initialized init result alone does not verify exclusion.

## Complete template cleanup and remaining directories

The migration inventories both committed template files and generated artifacts:

- Exact upstream `features/README.md`, `features/.gitkeep`, and
  `docs/adding-a-feature.md` are retired along with the feature infrastructure.
- A generated `wiki/WIKI-INDEX.md` loses only the migrated wiki's registration.
  If that was its only entry, the file is removed. Other wiki entries survive;
  a customized index that does not match the generated format is preserved.
- A template-format `.llm-wiki-template-log.md` is backed up and removed. An empty
  `.features-enabled` is removed after retiring its agent-comms entry; other
  feature registrations are preserved.
- Known template directories are removed bottom-up using `rmdir`, never recursive
  deletion. `.DS_Store`, `Thumbs.db`, and `Desktop.ini` files are backed up and removed only when they are the
  final residue in an otherwise removable template directory. Directory paths
  and original modes are recorded in the journal and restored on rollback.
- Unknown project files keep their containing directories. Preview and apply
  report preserved files in `scripts/`, `wiki/`, and `features/` explicitly.
  In particular, `scripts/agent-msg` is separate live-session messaging code,
  not the template agent-comms feature, and is not deleted merely for residing
  under `scripts/`.

This cleanup also works on a repository already migrated to `.llm-wiki`; rerun
preview to see the leftover-artifact cleanup plan. Do not assume every folder
named `scripts`, `wiki`, or `features` is exclusively template-owned.

For manual recovery, restore journaled directories in parent-first order with
recorded modes before restoring backed-up files and moving the wiki back. Empty
directory removal itself needs no Git staging; tracked artifact deletions are
included in `migration-paths.nul`.

## Customized CLAUDE.md and AGENTS.md

These shared instruction files no longer need to match upstream template prose.
The migration removes the contents and delimiters of the recognized
`lw:memory-boundary`, `lw:wiki-maintenance`, and `feature:agent-comms` blocks.
This includes customized text within those legacy regions. Unknown markers are
not treated as template-owned blocks.

Unmarked legacy sections such as `Wiki`, `Wiki maintenance behavior`, and
`Knowledge Graph` are removed, including their subsections, only when they
reference the old wiki location or retired template tooling. Elsewhere, lines
referencing retired tooling are removed; fenced examples and HTML comments
containing such references are removed as whole units to avoid leaving broken
syntax. Other old wiki path references are rewritten to `.llm-wiki`, and legacy
`/ask` invocations become `/wiki-ask`. Current plugin guidance is preserved.

The full original files are saved in the migration backup. Review the preview
and resulting diff, especially custom text inside legacy sections. Unbalanced,
nested or reversed recognized markers still stop before writes because their
boundaries are ambiguous. Customized executable scripts retain their existing
review protection; this change concerns shared instruction cleanup only.

## Clutter and leftover report

Preview prints a categorized inventory of content that would remain in template
locations. After successful apply, the tool performs a fresh scan and saves
`leftovers.txt` beside the backup journal and staging file, printing its path
and contents. The report distinguishes preserved project/unknown files,
retained clutter, symlinks, separate repositories, and unverified empty folders.
It scans `scripts`, `wiki`, `features`, `.claude`, `.cursor`, `.github` and `docs`;
a leftover is a review item, not proof of an active plugin conflict.

Desktop metadata (`.DS_Store`, `Thumbs.db`, `Desktop.ini`) can be removed when it
is all that prevents a verified retired template directory from disappearing.
The same applies to `.pyc`/`.pyo` files and `__pycache__` directories whose files
belong to known template Python modules. Unknown bytecode or cache contents,
symlinks, and directories containing project code are preserved and reported.
Cache contents are not mistaken for active instruction text.

Clutter bytes and directory modes participate in backup and rollback. No
recursive forced deletion is used, and the scan does not traverse symlinks or
nested Git repositories. Reports and staging files stay outside the project;
report-generation failure rolls back the migration rather than reporting success
without the promised inventory.
