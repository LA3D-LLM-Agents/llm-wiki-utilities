"""Retire legacy wiki instruction regions while retaining other project guidance."""
import re

MARKERS = ('lw:memory-boundary', 'lw:wiki-maintenance', 'feature:agent-comms')
TITLES = {'wiki', 'wiki maintenance behavior', 'wiki as memory', 'wiki as project memory',
          'memory boundary', 'knowledge graph', 'cross-agent communication'}


def clean(content, slug, removed, error):
    text = content.decode('utf-8')
    for marker in MARKERS:
        opening, closing = f'<!-- {marker} -->', f'<!-- /{marker} -->'
        if text.count(opening) != text.count(closing):
            raise error(f'Unbalanced legacy instruction markers: {marker}; repair boundaries before migration')
        # Only these known template regions are disposable; custom feature
        # markers and other lw regions retain their contents.
        pattern = re.escape(opening) + r'(?:(?!' + re.escape(opening) + r').)*?' + re.escape(closing)
        text = re.sub(pattern, '', text, flags=re.S)
        if opening in text or closing in text:
            raise error(f'Nested or reversed legacy instruction markers: {marker}')
    retired = set(removed) | {'wiki/init-wiki.sh', 'wiki/agents/', 'scripts/kg/',
                              'scripts/agent-comms/', 'llm-wiki.md',
                              'scripts/update-from-template.sh', 'scripts/enable-feature.sh',
                              'scripts/disable-feature.sh', 'scripts/check-template-version.sh'}
    def obsolete(value):
        return any(path in value for path in retired)

    # Heading boundaries inside fenced examples must not truncate a section.
    lines = text.splitlines(keepends=True)
    headings, fence = [], None
    for i, line in enumerate(lines):
        f = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if f:
            token = f[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            h = re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line)
            if h:
                headings.append((i, len(h[1]), h[2].strip().casefold()))
    drop = set()
    for n, (start, level, title) in enumerate(headings):
        end = next((i for i, depth, _ in headings[n + 1:] if depth <= level), len(lines))
        section = ''.join(lines[start:end])
        known = title in TITLES or title.startswith('cross-agent communication (')
        legacy_wiki = f'wiki/{slug}.wiki' in section
        if known and (obsolete(section) or legacy_wiki):
            drop.update(range(start, end))
    text = ''.join(line for i, line in enumerate(lines) if i not in drop)

    # Template-generation comments and fenced command examples form units.
    # Remove the whole unit if it calls retired tooling; do not leave broken
    # fences/comments or shell continuation fragments behind.
    text = re.sub(r'<!--.*?-->', lambda m: '' if obsolete(m[0]) else m[0], text, flags=re.S)
    lines = text.splitlines(keepends=True)
    result, i = [], 0
    while i < len(lines):
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})', lines[i])
        if match:
            token = match[1]
            end = i + 1
            while end < len(lines):
                if re.match(r'^\s{0,3}' + re.escape(token[0]) + '{' + str(len(token)) + r',}\s*$', lines[end]):
                    end += 1
                    break
                end += 1
            block = ''.join(lines[i:end])
            if not obsolete(block):
                result.append(block)
            i = end
        else:
            if not obsolete(lines[i]):
                result.append(lines[i])
            i += 1
    text = ''.join(result)
    text = re.sub(r'(?<![\w./-])wiki/' + re.escape(slug) + r'\.wiki(?=$|[^\w.-])', '.llm-wiki', text)
    text = re.sub(r'(?<![\w/])/ask(?![\w-])', '/wiki-ask', text)
    return text.encode('utf-8')
