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
- Removes unchanged upstream wiki instruction snippets from `CLAUDE.md` and
  `AGENTS.md`. Preserves custom prose and updates exact old wiki path references.
- Adds `/.llm-wiki/` to `.gitignore`, preserving existing rules.
- Checks retained active configuration, instructions, scripts and CI workflows
  for references to removed tooling or the old wiki path, stopping for review.

The script never commits, pushes, installs a plugin or updates wiki page contents.
Apply leaves the parent changes unstaged and prints the Git commands to review,
stage, and commit them. The printed `git add -A` stages unrelated changes too;
review first or use explicit paths. Install the appropriate plugin adapter
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
adds its ignore rule. It does not clean up hooks or instructions.

The old `naval-sensor-fusion.json` is retained solely as an optional historical
fixture. It is used only if explicitly passed with `--profile PATH`; general
migration does not load it.

## Tests

```sh
python3 -m unittest discover -s . -v
```

Tests use disposable repositories and the bundled upstream catalog; no Naval
Sensor Fusion checkout is required. They cover multiple identities, mixed custom
settings, managed prose, dirty wiki preservation, preview, repeat runs, collisions,
custom-file protection, symlinks, staged parent work and injected-failure rollback.
An optional historical-profile suite runs when `MIGRATION_SOURCE_REPO` points to
a checkout containing the old fixture commit; otherwise it is explicitly skipped.

## Agent communication: one active provider

The llm-wiki plugin currently provides `wiki-ask` and `wiki-enroll`. The legacy
agent-comms feature is therefore included in migration, not kept as a second
implementation. The catalog maps genuine upstream feature payloads to their
installed paths: `scripts/agent-comms/`, `.github/workflows/agent-comms.yml`,
`.claude/rules/feature-agent-comms.md`, and the upstream `.claude/commands/ask.md`.
Exact upstream feature sections in CLAUDE.md/AGENTS.md are removed too, and only
the `agent-comms` line is removed from `.features-enabled`. Unchanged feature
source files under `features/agent-comms/` are retired with the old installer.

Custom/unknown payloads and customized feature blocks stop the entire migration
for review, even when they contain no old-path references. Other features are
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
