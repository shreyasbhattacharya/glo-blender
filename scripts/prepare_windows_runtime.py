"""Extract pinned Microsoft app-local x64 runtimes (requires cabextract)."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

URL = 'https://aka.ms/vc14/vc_redist.x64.exe'
SHA256 = '9c02c9ed120f671b900e6b9471d890e3b2414117a7e53f4ae2e855466fa2b7c9'
DEST = Path(__file__).resolve().parents[1]/'bundled'/'vc_runtime'


def prepare():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        archive = root/'vc_redist.x64.exe'
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Microsoft runtime download changed; review and update the pinned hash.')
        subprocess.run(['cabextract', '-q', '-d', str(root/'installer'), str(archive)], check=True)
        subprocess.run(['cabextract', '-q', '-d', str(root/'dlls'),
                        str(root/'installer'/'a4')], check=True)
        DEST.mkdir(parents=True, exist_ok=True)
        for source in sorted((root/'dlls').glob('*.dll_amd64')):
            shutil.copyfile(source, DEST/source.name.removesuffix('_amd64'))
        # Preserve the license page linked by the official installer.
        with urllib.request.urlopen('https://aka.ms/VCRedistLicense', timeout=60) as response:
            (DEST/'MICROSOFT_VC_RUNTIME_LICENSE.html').write_bytes(response.read())
        (DEST/'source.json').write_text(json.dumps({
            'tool': 'Microsoft Visual C++ runtime (x64)',
            'version': '14.52.37029.0', 'url': URL, 'sha256': SHA256,
        }, indent=2) + '\n')


if __name__ == '__main__':
    prepare()
