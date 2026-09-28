"""Conservative, repository-independent template ownership and shared-file edits."""
import base64
import json
from pathlib import Path
import re
import subprocess
from claude_cleanup import clean as clean_instructions

SOURCE_URL = 'https://github.com/crcresearch/llm-wiki-memory-template'
SHARED = {'wiki/.gitignore', 'CLAUDE.md', 'AGENTS.md', 'README.md', '.gitignore', '.claude/settings.json', '.claude/settings.local.json'}
HOOKS = {
    '.claude/hooks/ensure-wiki.py': 'wiki/agents/claude-code/templates/ensure-wiki.py',
    '.claude/hooks/session-start.sh': 'wiki/agents/claude-code/templates/session-start-hook.sh',
    '.claude/hooks/posttooluse-hook.sh': 'wiki/agents/claude-code/templates/posttooluse-hook.sh',
}


def owned(name):
    return (name not in SHARED and (
        name in {'llm-wiki.md', 'features/README.md', 'features/.gitkeep', 'docs/adding-a-feature.md', '.cursorrules'} or name.startswith(('wiki/', 'scripts/', '.claude/', '.cursor/', 'features/agent-comms/'))
        or name in {'.github/workflows/test-harness.yml', '.github/workflows/agent-comms.yml', 'CLAUDE.md.template', 'README.md.template', '.cursorrules.template'}))


def reference_catalog(repo, all_history=False):
    """Read committed upstream bytes; never execute reference shell scripts."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    commits = git('rev-list', 'HEAD').decode().splitlines() if all_history else [git('rev-parse', 'HEAD').decode().strip()]
    paths = {}
    for commit in commits:
        for record in git('ls-tree', '-r', '-z', commit).split(b'\0'):
            if not record:
                continue
            metadata, raw_name = record.split(b'\t', 1)
            mode, kind, oid = metadata.decode().split()
            name = raw_name.decode()
            if kind != 'blob' or mode not in ('100644', '100755'):
                continue
            if owned(name) or name in {'CLAUDE.md', '.claude/settings.json', 'wiki/.gitignore'}:
                paths.setdefault(name, set()).add(oid)
    objects = sorted({oid for values in paths.values() for oid in values})
    batch = subprocess.run(['git', '-C', str(repo), 'cat-file', '--batch'],
                           input=('\n'.join(objects) + '\n').encode(),
                           stdout=subprocess.PIPE, check=True).stdout
    contents = {}
    offset = 0
    for oid in objects:
        end = batch.index(b'\n', offset)
        header = batch[offset:end].split()
        if header[:2] != [oid.encode(), b'blob']:
            raise ValueError('Unexpected Git object response')
        size = int(header[2])
        raw = batch[end + 1:end + 1 + size]
        contents[oid] = base64.b64encode(raw).decode()
        offset = end + 2 + size
    files = {name: sorted({contents[oid] for oid in values}) for name, values in paths.items()}
    return {'version': 1, 'source': SOURCE_URL, 'commits': commits, 'files': files}


def render_variants(raw, slug):
    # Both placeholder forms have been used by upstream setup/instantiate.
    return {raw, raw.replace(b'{{REPO_NAME}}', slug.encode()),
            raw.replace(b'${REPO_NAME}', slug.encode()),
            raw.replace(b'{{REPO_NAME}}', slug.encode()).replace(b'${REPO_NAME}', slug.encode())}


def cleanup(root, source, target, slug, catalog, safe_path, error):
    slug = slug or (source.name[:-5] if source else '')
    if not slug and target.is_dir():
        schemas = sorted(target.glob('SCHEMA_*.md'))
        if len(schemas) == 1:
            slug = schemas[0].stem[len('SCHEMA_'):]
    if not slug:
        slug = root.name
    originals = {name: {variant for encoded in versions
                        for variant in render_variants(base64.b64decode(encoded, validate=True), slug)}
                 for name, versions in catalog['files'].items()}
    originals.setdefault('.cursorrules', set()).update(originals.get('.cursorrules.template', set()))
    for installed, template in HOOKS.items():
        originals.setdefault(installed, set()).update(originals.get(template, set()))
    # Map the separately installed feature payload to its upstream bytes.
    # Never run disable-feature.sh: it recursively deletes customized files.
    for name, variants in list(originals.items()):
        installed = None
        if name.startswith('features/agent-comms/code/'):
            installed = 'scripts/agent-comms/' + name[len('features/agent-comms/code/'):]
        elif name == 'features/agent-comms/ci/agent-comms.yml':
            installed = '.github/workflows/agent-comms.yml'
        elif name == 'features/agent-comms/rule.md':
            installed = '.claude/rules/feature-agent-comms.md'
        if installed:
            originals.setdefault(installed, set()).update(variants)
    if source is None and not target.exists() and not any(safe_path(root, name).exists() for name in ('llm-wiki.md', 'wiki/init-wiki.sh')):
        raise error('No legacy wiki, plugin wiki, or template marker found')
    changes = {}
    conflicts = []
    def read(name):
        p = safe_path(root, name)
        if not p.exists():
            return None
        if not p.is_file():
            raise error(f'Expected regular file: {name}')
        return p.read_bytes()
    for name, variants in originals.items():
        if not owned(name):
            continue
        before = read(name)
        if before is None:
            continue
        if before not in variants:
            conflicts.append(f'{name}: customized or from an unrecognized template revision')
        else:
            changes[name] = (before, None)

    # The migration owns legacy wiki instruction regions even when their
    # wording was customized. Other project prose and unknown markers remain.
    retired = [name for name, (_, after) in changes.items() if after is None]
    for name in ('CLAUDE.md', 'AGENTS.md'):
        before = read(name)
        if before is None:
            continue
        after = clean_instructions(before, slug, retired, error)
        if after != before:
            changes[name] = (before, after)

    # Only exact upstream allow entries dedicated to legacy wiki tooling are
    # removable. Generic Python permissions and user allow/deny/ask remain.
    template_permissions = set()
    for name in ('.claude/settings.json.template', '.claude/settings.json'):
        for raw in originals.get(name, set()):
            defaults = json.loads(raw)
            for entry in defaults.get('permissions', {}).get('allow', []):
                if any(path in entry for path in (f'wiki/{slug}.wiki',
                        'wiki/{{REPO_NAME}}.wiki', 'wiki/${REPO_NAME}.wiki',
                        'wiki/init-wiki.sh', 'scripts/kg/', 'wiki/agents/')):
                    template_permissions.add(entry)

    # Remove only known complete hook commands, retaining unrelated hooks,
    # matcher metadata, permissions, environment and plugin configuration.
    commands = set(HOOKS)
    commands.add('command -v python3 >/dev/null 2>&1 && python3 .claude/hooks/ensure-wiki.py || true')
    for name in ('.claude/settings.json', '.claude/settings.local.json'):
        before = read(name)
        if before is None:
            continue
        data = json.loads(before)
        if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
            raise error(f'{name}: invalid settings/hooks object')
        changed = False
        permissions = data.get('permissions', {})
        if not isinstance(permissions, dict):
            raise error(f'{name}: invalid permissions object')
        if 'allow' in permissions:
            allow = permissions['allow']
            if not isinstance(allow, list) or not all(isinstance(entry, str) for entry in allow):
                raise error(f'{name}: invalid permissions.allow list')
            filtered = [entry for entry in allow if entry not in template_permissions]
            if filtered != allow:
                permissions['allow'] = filtered
                changed = True
        for event, groups in list(data.get('hooks', {}).items()):
            if not isinstance(groups, list):
                raise error(f'{name}: invalid hook groups')
            kept_groups = []
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                    raise error(f'{name}: invalid hook group')
                kept = []
                for hook in group['hooks']:
                    if not isinstance(hook, dict):
                        raise error(f'{name}: invalid hook')
                    command = hook.get('command', '')
                    if hook.get('type') == 'command' and command in commands:
                        changed = True
                    else:
                        if any(path in str(command) for path in HOOKS):
                            conflicts.append(f'{name}: custom legacy hook command: {command}')
                        kept.append(hook)
                if kept or not group['hooks']:
                    kept_groups.append(dict(group, hooks=kept))
            if kept_groups:
                data['hooks'][event] = kept_groups
            else:
                del data['hooks'][event]
        if changed:
            if 'hooks' in data and not data['hooks']:
                del data['hooks']
            changes[name] = (before, (json.dumps(data, indent=2) + '\n').encode())

    before = read('.features-enabled')
    if before is not None:
        after = b''.join(line for line in before.splitlines(keepends=True)
                         if line.strip() != b'agent-comms')
        if after != before:
            changes['.features-enabled'] = (before, after if after.strip() else None)

    # init-wiki.sh generates this registry; it is not a tracked template blob.
    before = read('wiki/WIKI-INDEX.md')
    if before is not None:
        header = '---\ntype: index\n---\n\n# Wiki Index — wiki\n\n## Wikis\n'.encode()
        if before.startswith(header):
            lines = before[len(header):].splitlines(keepends=True)
            if all(re.fullmatch(r'- \[\[Home_[^\]\r\n]+\]\] — [^\r\n]+\n?'.encode(), line) for line in lines):
                prefix = f'- [[Home_{slug}]] — '.encode()
                kept = [line for line in lines if not line.startswith(prefix)]
                if kept != lines:
                    changes['wiki/WIKI-INDEX.md'] = (before, header + b''.join(kept) if kept else None)
    before = read('.llm-wiki-template-log.md')
    if before is not None:
        pattern = (rb'(?:# llm-wiki template sync log\n\n)?'
                   rb'(?:## \[\d{4}-\d{2}-\d{2}\] pulled template @[0-9a-f]+ - \d+ file\(s\) updated\n'
                   rb'(?:- [^\r\n]+\n)*\n*)+')
        if re.fullmatch(pattern, before):
            changes['.llm-wiki-template-log.md'] = (before, None)

    # Unknown/custom feature files and blocks must not silently survive as
    # a second implementation alongside the plugin's ask/enroll skills.
    feature_dir = safe_path(root, 'scripts/agent-comms')
    if feature_dir.is_dir():
        for path in feature_dir.rglob('*'):
            if path.is_file() or path.is_symlink():
                name = str(path.relative_to(root))
                if name not in changes:
                    conflicts.append(f'{name}: unrecognized/custom agent-comms payload; plugin provides ask/enroll')
    for name in ('CLAUDE.md', 'AGENTS.md'):
        after = changes[name][1] if name in changes else read(name)
        if after and b'<!-- feature:agent-comms -->' in after:
            conflicts.append(f'{name}: customized agent-comms block; reconcile with plugin ask/enroll')

    # Do not leave active instructions or hooks pointing at removed tooling.
    active = set(SHARED - {'README.md', '.gitignore', 'wiki/.gitignore'})
    for folder in ('.claude', '.cursor', 'scripts', '.github/workflows'):
        directory = safe_path(root, folder)
        if directory.exists():
            active.update(str(p.relative_to(root)) for p in directory.rglob('*') if p.is_file())
    removed = [name.encode() for name, (_, after) in changes.items() if after is None]
    old_wiki = f'wiki/{slug}.wiki'.encode()
    for name in sorted(active):
        after = changes[name][1] if name in changes else read(name)
        if after is not None and (old_wiki in after or any(path in after for path in removed)):
            conflicts.append(f'{name}: retained instructions/hooks reference legacy wiki or removed tooling')
    if conflicts:
        raise error('Migration needs review; no changes made:\n  ' + '\n  '.join(conflicts))
    return [(name, before, after) for name, (before, after) in sorted(changes.items())]
