# llm-wiki utilities

Standalone tools for maintaining and migrating llm-wiki projects.
Each utility has its own directory, README, dependencies, and tests.

## Utilities

| Utility | Purpose |
| --- | --- |
| [Wiki plugin migration](utilities/wiki-plugin-migration/README.md) | Preview and migrate a template-based project to the llm-wiki plugin, including legacy agent-comms cleanup. |

## Quick start

```sh
git clone https://github.com/LA3D-LLM-Agents/llm-wiki-utilities.git
cd llm-wiki-utilities/utilities/wiki-plugin-migration
python3 migrate.py /path/to/project --verbose
```

Migration defaults to a read-only preview. Read the utility's README before
using `--apply`; customized files may require manual review. The tool does not
install plugins, commit changes, or push repositories.

## Adding a utility

Create `utilities/<utility-name>/` with:

- `README.md`: purpose, requirements, examples, inputs/outputs, side effects,
  recovery where relevant, and validation instructions.
- The utility's scripts and any supporting data.
- Tests or a documented validation procedure.

Keep utility-specific dependencies and configuration in that directory, use
paths relative to the script instead of the caller's working directory for
bundled resources, and add the utility to the table above. Keep generated output,
credentials, personal settings, and local caches out of Git. Add a dedicated CI
job when the utility has automated tests.

There is no shared installation requirement: use each utility independently.

## Related projects

- [llm-wiki template](https://github.com/crcresearch/llm-wiki-memory-template)
- [llm-wiki plugin](https://github.com/LA3D-LLM-Agents/llm-wiki-colab)
