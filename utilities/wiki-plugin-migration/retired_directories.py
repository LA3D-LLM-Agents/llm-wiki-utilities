"""Plan non-recursive removal of emptied, known template directories."""
import base64
from pathlib import PurePosixPath

ROOTS = ('scripts', 'wiki', 'features', '.claude', '.cursor', '.github', 'docs')


def plan(root, changes, source, catalog, safe_path):
    planned = {name: (before, after) for name, before, after in changes}
    known = set(ROOTS)
    for name in list(catalog['files']) + list(planned):
        if PurePosixPath(name).parts[0] in ROOTS:
            known.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
    directories = []

    def visit(relative):
        path = safe_path(root, relative)
        if not path.is_dir():
            return False
        if source is not None and path == source:
            return True  # Renamed by the transaction, not recursively traversed.
        if (path / '.git').exists():
            return False
        removable = True
        metadata = []
        for child in sorted(path.iterdir()):
            name = str(child.relative_to(root))
            if child.is_symlink():
                removable = False
            elif child.is_dir():
                if source is not None and child == source:
                    continue
                if name not in known or not visit(name):
                    removable = False
            elif name in planned and planned[name][1] is None:
                continue
            elif child.name == '.DS_Store':
                metadata.append((name, child.read_bytes()))
            elif name == 'wiki/.gitignore' and child.read_bytes() in {
                    base64.b64decode(x) for x in catalog['files'].get(name, [])}:
                metadata.append((name, child.read_bytes()))
            else:
                removable = False
        if removable:
            for name, before in metadata:
                planned[name] = (before, None)
            directories.append({'path': relative, 'mode': path.stat().st_mode & 0o777})
        return removable

    for name in ROOTS:
        if (root / name).is_dir():
            visit(name)
    return [(name, before, after) for name, (before, after) in sorted(planned.items())], directories


def remaining_files(root, changes=(), source=None):
    """Report remaining content, never infer it is disposable from its directory."""
    result = []
    deleted = {name for name, _, after in changes if after is None}
    def visit(path):
        if path == source or str(path.relative_to(root)) in deleted:
            return
        if path.is_symlink():
            result.append(str(path.relative_to(root)))
        elif path.is_dir():
            if (path / '.git').exists():
                result.append(str(path.relative_to(root)) + '/ (separate repository)')
            else:
                for child in sorted(path.iterdir()):
                    visit(child)
        else:
            result.append(str(path.relative_to(root)))
    for name in ('scripts', 'wiki', 'features'):
        path = root / name
        if path.exists():
            visit(path)
    return result
