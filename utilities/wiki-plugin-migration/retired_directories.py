"""Plan non-recursive removal of emptied, known template directories."""
import base64
from pathlib import PurePosixPath

CLUTTER_NAMES = {'.DS_Store', 'Thumbs.db', 'Desktop.ini'}


def is_clutter(path):
    return path.name in CLUTTER_NAMES or path.suffix in {'.pyc', '.pyo'}


ROOTS = ('scripts', 'wiki', 'features', '.claude', '.cursor', '.github', 'docs')


def plan(root, changes, source, catalog, safe_path):
    planned = {name: (before, after) for name, before, after in changes}
    known = set(ROOTS)
    for name in list(catalog['files']) + list(planned):
        if PurePosixPath(name).parts[0] in ROOTS:
            known.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
    directories = []
    modules = {}
    for name in list(catalog['files']) + [name for name, _, after in changes if after is None]:
        path = PurePosixPath(name)
        if path.suffix == '.py':
            modules.setdefault(str(path.parent), set()).add(path.stem)

    def known_bytecode(path, parent):
        return path.suffix in {'.pyc', '.pyo'} and any(
            path.name == module + path.suffix or path.name.startswith(module + '.')
            for module in modules.get(parent, set()))

    def cache_plan(path, parent):
        # Bytecode must belong to a cataloged template module. Never recurse
        # through arbitrary cache trees, symlinks, or repository boundaries.
        files = []
        for child in sorted(path.iterdir()):
            if child.is_symlink() or not child.is_file():
                return None
            if child.name not in CLUTTER_NAMES and not known_bytecode(child, parent):
                return None
            files.append((str(child.relative_to(root)), child.read_bytes()))
        return files, {'path': str(path.relative_to(root)), 'mode': path.stat().st_mode & 0o777}


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
        cache_dirs = []
        for child in sorted(path.iterdir()):
            name = str(child.relative_to(root))
            if child.is_symlink():
                removable = False
            elif child.is_dir():
                if source is not None and child == source:
                    continue
                if child.name == '__pycache__':
                    cache = cache_plan(child, relative)
                    if cache is None:
                        removable = False
                    else:
                        metadata.extend(cache[0])
                        cache_dirs.append(cache[1])
                    continue
                if name not in known or not visit(name):
                    removable = False
            elif name in planned and planned[name][1] is None:
                continue
            elif child.name in CLUTTER_NAMES or known_bytecode(child, relative):
                metadata.append((name, child.read_bytes()))
            elif name == 'wiki/.gitignore' and child.read_bytes() in {
                    base64.b64decode(x) for x in catalog['files'].get(name, [])}:
                metadata.append((name, child.read_bytes()))
            else:
                removable = False
        if removable:
            for name, before in metadata:
                planned[name] = (before, None)
            directories.extend(cache_dirs)
            directories.append({'path': relative, 'mode': path.stat().st_mode & 0o777})
        return removable

    for name in ROOTS:
        if (root / name).is_dir():
            visit(name)
    return [(name, before, after) for name, (before, after) in sorted(planned.items())], directories


def remaining_files(root, changes=(), source=None, directories=(), roots=None):
    """Report remaining content, never infer it is disposable from its directory."""
    result = []
    deleted = {name for name, _, after in changes if after is None}
    pruned = {entry['path'] for entry in directories}
    def visit(path):
        if path == source or str(path.relative_to(root)) in deleted | pruned:
            return
        if path.is_symlink():
            result.append(str(path.relative_to(root)) + ' [symlink; preserved]')
        elif path.is_dir():
            if (path / '.git').exists():
                result.append(str(path.relative_to(root)) + '/ (separate repository)')
            else:
                children = sorted(path.iterdir())
                if not children:
                    result.append(str(path.relative_to(root)) + '/ [empty directory; not verified as retired]')
                for child in children:
                    visit(child)
        else:
            label = 'clutter retained; directory or cache ownership needs review' if is_clutter(path) else 'project/unknown file; preserved'
            result.append(str(path.relative_to(root)) + ' [' + label + ']')
    for name in (roots if roots is not None else ROOTS):
        path = root / name
        if path.exists() or path.is_symlink():
            visit(path)
    return result
