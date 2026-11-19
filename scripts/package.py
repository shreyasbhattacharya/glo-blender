"""Build install-from-disk releases; Windows archives retain their dependencies."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
INFO = ast.literal_eval(ast.parse((ROOT/'__init__.py').read_text()).body[1].value)
VERSION = '.'.join(map(str, INFO['version']))
FILES = ['__init__.py', 'import_bridge.py', 'job_runner.py', 'reconstruction.py',
         'toolchain.py', 'README.md', 'docs/technical-guide.md', 'LICENSE', 'ready.png']


def build(output, colmap=None, ffmpeg=None):
    if bool(colmap) != bool(ffmpeg):
        raise ValueError('Supply both Windows tool archives')
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
                for member in tool.infolist():
                    path = PurePosixPath(member.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in member.filename:
                        raise ValueError(f'Unsafe archive member: {member.filename}')
                    if not member.is_dir():
                        bundle.writestr(f'blender_glomap/bundled/tools/windows-x64/{name}/{path}',
                                        tool.read(member))
        if sources:
            bundle.writestr('blender_glomap/bundled/tools/windows-x64/sources.json',
                            json.dumps(sources, indent=2))
    with ZipFile(destination) as bundle:
        if bundle.testzip():
            raise RuntimeError('Archive integrity check failed')
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
