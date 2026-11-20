"""Build install-from-disk releases; Windows archives retain their dependencies."""
import argparse
import ast
import hashlib
import io
import importlib.util
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
INFO = ast.literal_eval(ast.parse((ROOT/'__init__.py').read_text()).body[1].value)
VERSION = '.'.join(map(str, INFO['version']))
FILES = ['__init__.py', 'import_bridge.py', 'job_runner.py', 'reconstruction.py',
         'toolchain.py', 'windows_dll.py', 'README.md', 'docs/technical-guide.md', 'LICENSE', 'ready.png']
spec = importlib.util.spec_from_file_location('windows_dll', ROOT/'windows_dll.py')
windows_dll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows_dll)
RUNTIME = ROOT/'bundled'/'vc_runtime'
REQUIRED_RUNTIME = {
    'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll', 'vcomp140.dll',
}


def runtime_files():
    files = sorted(RUNTIME.glob('*.dll'))
    missing = REQUIRED_RUNTIME - {p.name.lower() for p in files}
    if missing:
        raise ValueError(f'Missing Windows runtime DLLs: {", ".join(sorted(missing))}. '
                         'Run scripts/prepare_windows_runtime.py first.')
    for path in files:
        data = path.read_bytes()
        offset = int.from_bytes(data[60:64], 'little')
        if (data[:2] != b'MZ' or data[offset:offset + 6] != b'PE\0\0\x64\x86'):
            raise ValueError(f'Expected an x64 Windows DLL: {path}')
    return files


def audit_windows_bundle(bundle):
    """Reject missing transitive dependencies, including DLLs used by plugins."""
    prefix = 'blender_glomap/bundled/tools/windows-x64/'
    for tool in ('colmap', 'ffmpeg'):
        binaries = {PurePosixPath(n).name.lower(): n for n in bundle.namelist()
                    if n.startswith(prefix + tool + '/') and n.lower().endswith(('.dll', '.exe'))}
        for name, path in binaries.items():
            machine, imports = windows_dll.pe_imports(bundle.read(path))
            if machine != 0x8664:
                raise ValueError(f'Non-x64 Windows binary: {path}')
            missing = [dll for dll in imports
                       if dll not in binaries and not windows_dll.is_system_dll(dll)]
            if missing:
                raise ValueError(f'{name} requires missing DLLs: {", ".join(missing)}')


def build(output, colmap=None, ffmpeg=None):
    if bool(colmap) != bool(ffmpeg):
        raise ValueError('Supply both Windows tool archives')
    runtime = runtime_files() if colmap else []
    output.mkdir(parents=True, exist_ok=True)
    suffix = '-windows-x64' if colmap else ''
    destination = output/f'blender_glomap-v{VERSION}{suffix}.zip'
    with ZipFile(destination, 'w', ZIP_DEFLATED) as bundle:
        for path in [*(ROOT/name for name in FILES), *sorted((ROOT/'bundled').glob('*'))]:
            if path.is_file():
                bundle.write(path, 'blender_glomap/' + path.relative_to(ROOT).as_posix())
        sources = []
        for name, archive in [('colmap', colmap), ('ffmpeg', ffmpeg)]:
            if not archive:
                continue
            data = archive.read_bytes()
            sources.append({'tool': name, 'archive': archive.name,
                            'sha256': hashlib.sha256(data).hexdigest()})
            with ZipFile(io.BytesIO(data)) as tool:
                expected = ['colmap.exe'] if name == 'colmap' else ['ffmpeg.exe', 'ffprobe.exe']
                for exe in expected:
                    if sum(PurePosixPath(n).name == exe for n in tool.namelist()) != 1:
                        raise ValueError(f'{archive}: expected exactly one {exe}')
                exe_dir = PurePosixPath(next(n for n in tool.namelist()
                                             if PurePosixPath(n).name == expected[0])).parent
                for member in tool.infolist():
                    path = PurePosixPath(member.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in member.filename:
                        raise ValueError(f'Unsafe archive member: {member.filename}')
                    if not member.is_dir():
                        if name == 'colmap' and path.parent == exe_dir and path.name.lower() in {
                                p.name.lower() for p in runtime}:
                            continue
                        bundle.writestr(f'blender_glomap/bundled/tools/windows-x64/{name}/{path}',
                                        tool.read(member))
                if name == 'colmap':
                    for path in runtime:
                        bundle.write(path, f'blender_glomap/bundled/tools/windows-x64/colmap/{exe_dir}/{path.name}')
                    for path in sorted(RUNTIME.glob('*.html')):
                        bundle.write(path, f'blender_glomap/bundled/tools/windows-x64/colmap/{path.name}')
                    sources.append(json.loads((RUNTIME/'source.json').read_text()))
        if sources:
            bundle.writestr('blender_glomap/bundled/tools/windows-x64/sources.json',
                            json.dumps(sources, indent=2))
    with ZipFile(destination) as bundle:
        if bundle.testzip():
            raise RuntimeError('Archive integrity check failed')
        if colmap:
            audit_windows_bundle(bundle)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n')
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'deliverables')
    parser.add_argument('--colmap', type=Path)
    parser.add_argument('--ffmpeg', type=Path)
    args = parser.parse_args()
    build(args.output, args.colmap, args.ffmpeg)
