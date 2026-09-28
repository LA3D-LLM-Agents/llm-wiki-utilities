# Validation — 2026-09-26

## Current generic package

`python3 -m unittest discover -s /private/tmp/wiki-plugin-migration -v`:
22 generic tests passed; 13 optional historical-profile tests were skipped
because their external fixture was not supplied. These skips are not passes.

The permission regression reproduced the original failure before the fix.
After the fix, mixed stock/custom permissions migrate in both settings.json
and settings.local.json. Generic Python allow entries, custom allow entries,
deny/ask lists and environment are preserved. A customized permission naming
the legacy wiki still blocks atomically rather than being silently removed.
An immediate repeat remains a no-op.

The catalog was rebuilt from the genuine upstream template Git history, with
144 commits reachable from HEAD 49f0fc3eb66aec500d16c74f54240200f70774f3 and
249 catalog paths. Included historical sync revisions:
- 55d94d95e4fa5f31f955cc46d74f9a86535caaa1
- 67696a55f1a60d8630c46c95e8b8f6518510546e
- 879c41363251746bb0167d3765cb644658d2d807

c39d65ae2b5995b0792c810123eb14b527570076 is a downstream cleanup commit,
not an upstream template release. No downstream project bytes were added to
the generic catalog.

## Real checkout, read-only preview

Previewing the existing naval-sensor-fusion checkout with the default catalog
cleared the settings.json permission blocker. It still refused migration:
five files did not match upstream variants (scripts/kg/wiki-to-jsonld.py,
scripts/wiki-reciprocity.py and the three .claude/commands/wiki-*.md files).
Retained references also blocked in CLAUDE.md, agent-comms scripts/docs and
some of those unmatched files. This is not a successful real-repo migration.
No --apply was used against the existing checkout.

## Earlier fixture check

Before this review revision, a disposable upstream HEAD inventory fixture with
an unrelated parent name and galaxy-model.wiki passed preview/apply/repeat.
That fixture did not exercise all generated stock settings. The newly added
permission regression covers that previously missing case.

Existing project repositories and their wikis were not migrated or edited.
Plugin installation and live agent startup remain untested. Arbitrary custom
instances may require explicit review; the package does not claim universal
automatic migration. The standalone package and temporary reference clone
are the only maintained outputs of this revision.

## Agent-comms follow-up

The installed llm-wiki 0.4.1 skill files confirm wiki-ask/wiki-enroll are supplied
by the plugin. The catalog now includes upstream feature sources and maps their
installed payload, workflow and rule paths. Added tests cover removal of stock
legacy commands/payloads/instructions, preservation of another enabled feature
and an existing Card, and atomic rejection of customized scripts or blocks.

A new read-only preview of the local naval-sensor-fusion checkout no longer flags
scripts/agent-comms/README.md or enroll.sh. The five unmatched files and remaining
CLAUDE.md/legacy-reference conflicts described above still block this local
checkout. No migration was applied. Different reviewers' checkouts can have
different conflicts; no claim is made that all naval checkouts are identical.
