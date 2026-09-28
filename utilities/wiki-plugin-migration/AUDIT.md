# Template/plugin compatibility audit — 2026-09-27

Inspected genuine upstream template commit
`49f0fc3eb66aec500d16c74f54240200f70774f3` and plugin commit
`f45edcf83b9b1790305086a67a779e016af26b3f`. Findings were reproduced in
disposable repositories; no existing project wiki was initialized or migrated.

## Confirmed plugin issues (not changed upstream)

1. **Init can create a second namespace after a host rename.**
   `initialize()` derives its name from the host origin (or directory), and
   `has_schema()` only checks that name and bare `SCHEMA.md`. Startup's
   `wiki_name()` instead inspects the stamped index. With an existing
   `SCHEMA_ocean_research-42.md` and host origin renamed to `renamed-project`,
   actual plugin init returned `scaffolded`, created a second schema/index/log
   family, and changed wiki HEAD. Passing `--repo-name ocean_research-42` returned
   `already-initialized` and preserved HEAD and dirty wiki work. Migration now
   prints the preserved namespace and the explicit override to use if init is
   needed. Migration itself never invokes initialization or renames wiki pages.
2. **Already-initialized shortcut skips exclusion and identity maintenance.**
   It returns before `ensure_exclude()` and `seed_identity()`. Removing the local
   ignore rule and rerunning init with an existing schema returned success while
   the wiki remained unignored. Running the actual startup exclusion helper
   repaired it. Missing local identity seeding on this branch is established by
   code inspection, not a separate runtime test. Migration ensures exclusion
   independently and preserves existing wiki configuration.

The init implementation is byte-identical across Claude, Codex and Cursor
adapters at this plugin revision. Runtime reproductions used the Codex adapter.
These findings need upstream fixes; no plugin/template commits were made here.

## Confirmed migration defects fixed

- An already-attached `.llm-wiki` committed as an embedded Git repository in the
  parent was accepted in wiki-only mode. Ignore rules do not untrack it. The
  migration now checks the parent index and refuses this attachment, just as it
  already refused a tracked legacy wiki. It does not silently alter the index.
- Removing the template's `wiki/.gitignore` exposed an unselected legacy wiki
  when migrating one of several. A real `git check-ignore` assertion failed
  before the fix. Root `.gitignore` and `wiki/.gitignore` are now preserved in
  all modes, including explicit historical profiles.

## Important behavior differences

- The template init can update an existing schema; plugin init is attachment/
  scaffolding, not an upgrade operation. Migration preserves wiki contents.
  Old schema prose or page links to retired tooling need a separate content
  review, not automatic template overwriting.
- Plugin init accepts bare `SCHEMA.md`; startup orientation still names
  `SCHEMA_<namespace>.md` and reads namespaced index/log. The migration now
  reports bare/missing/ambiguous schemas and missing navigation files for review.
  It does not claim that relocation alone guarantees a complete memory snapshot.
- `wiki-init` asks for GitHub versus offline storage unless already chosen;
  migration must not choose an offline backend or reinitialize existing memory
  automatically. Existing wiki remotes, history, cards and identity are retained.
- The plugin's `--stamp-missing-templates` adds only the missing vocabulary
  template (`Edge-Types`), not a wholesale upgrade of legacy pages.

## Reproduce

```sh
WIKI_PLUGIN_ROOT=/path/to/llm-wiki-colab/codex/plugins/llm-wiki \
  python3 -m unittest discover -s utilities/wiki-plugin-migration -v
```

At the audited versions: 28 generic migration tests and 3 actual-plugin audit
checks passed; 13 optional historical-profile tests were skipped. Two plugin
checks intentionally reproduce the unfixed issues above; their passing means
reproduction succeeded, not that the upstream issues are resolved. These checks
are skipped unless WIKI_PLUGIN_ROOT is supplied and should be reassessed when
the plugin revision changes. Live GitHub attachment/authentication, agent UI
startup and every historical template instantiation were not tested.

## Sources

- [Plugin wiki-init skill](https://github.com/LA3D-LLM-Agents/llm-wiki-colab/blob/f45edcf83b9b1790305086a67a779e016af26b3f/codex/plugins/llm-wiki/skills/wiki-init/SKILL.md)
- [Plugin initialization](https://github.com/LA3D-LLM-Agents/llm-wiki-colab/blob/f45edcf83b9b1790305086a67a779e016af26b3f/codex/plugins/llm-wiki/skills/wiki-init/scripts/init-wiki.py)
- [Startup orientation](https://github.com/LA3D-LLM-Agents/llm-wiki-colab/blob/f45edcf83b9b1790305086a67a779e016af26b3f/codex/plugins/llm-wiki/hooks/session-start.d/30-build-orientation.py)
- [Template initialization](https://github.com/crcresearch/llm-wiki-memory-template/blob/49f0fc3eb66aec500d16c74f54240200f70774f3/wiki/init-wiki.sh)
