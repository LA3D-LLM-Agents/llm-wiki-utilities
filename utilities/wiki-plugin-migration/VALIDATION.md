# Validation

The general migration test suite uses synthetic disposable repositories and the
public upstream template catalog. No project-specific profiles, replacement
contents or private wiki fixtures belong in this repository.

Run from the repository root:

```sh
python3 -m unittest discover -s utilities/wiki-plugin-migration -q
```

Coverage includes generic template cleanup, customized shared instructions,
local Git exclusions, wiki history preservation, generated template artifacts,
clutter handling, scoped staging, backup and rollback. Optional actual-plugin
checks require WIKI_PLUGIN_ROOT; see AUDIT.md. They are skipped by default.

2026-09-28 validation after removing the legacy profile mechanism: 44 generic
tests passed, 3 optional plugin checks skipped. No existing project was migrated.
