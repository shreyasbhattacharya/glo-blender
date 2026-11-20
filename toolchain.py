"""Install a CPU-capable tool environment from conda-forge."""
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import tarfile
import tempfile
import urllib.request

from .reconstruction import Pipeline, WindowsLoaderError

BOOTSTRAP = {
    'linux-64': '3e360875eac0d8585b926098ba1450c541ea7bbd3ee142b3d5115659f9f588f8',
    'linux-aarch64': 'cef17b5743140670461a6a07291bb3c39ac506354f63ba9a734034de65a3f994',
    'osx-64': '156440bca0699871c2b504e87c65dd3b6004941556579213ffdb217f35485343',
    'osx-arm64': 'bb5767ef07c9801cdf8bc36e1f9b7319736ce7a02f2c46f8e6e7f4aeb6ef9165',
    'win-64': 'b2648ee0a873e916b1bfbbcb00e3f4cbd546d6295247c62fd453876c788c0a78',
}
CHECKS = {'colmap': ['help'], 'ffmpeg': ['-version'], 'ffprobe': ['-version']}
COLMAP_VERSION = '4.2.1'


def bundled_tools():
    base = Path(__file__).parent/'bundled'/'tools'/'windows-x64'
    return base if target() == 'win-64' and base.is_dir() else None


def bundled_executable(name):
    base = bundled_tools()
    if base:
        matches = sorted((base/('colmap' if name == 'colmap' else 'ffmpeg')).rglob(f'{name}.exe'))
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f'Bundled {name}.exe missing or ambiguous. Re-extract the Windows release.')


def prepare_bootstrap_runtime(folder):
    """Micromamba itself needs the VC runtime before it can install packages."""
    if target() != 'win-64' or not bundled_tools():
        return
    source = bundled_executable('colmap').parent
    for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        if not (source/name).is_file():
            raise RuntimeError(f'Bundled runtime missing: {source/name}. Reinstall the current Windows ZIP.')
    for pattern in ('msvcp140*.dll', 'vcruntime140*.dll', 'concrt140.dll'):
        for path in source.glob(pattern):
            shutil.copy2(path, folder/path.name)


def target():
    machine = platform.machine().lower()
    arch = '64' if machine in ('x86_64', 'amd64') else 'arm64' if machine in ('aarch64', 'arm64') else machine
    system = {'Windows': 'win', 'Darwin': 'osx', 'Linux': 'linux'}.get(platform.system(), '')
    return f'{system}-{("aarch64" if system == "linux" and arch == "arm64" else arch)}'


def prefix(root):
    base = root/'envs'/f'colmap-{COLMAP_VERSION}-{target()}'
    if not (base/'.glomap-ready').is_file():
        for candidate in sorted(base.parent.glob(f'{base.name}-*')):
            suffix = candidate.name[len(base.name) + 1:]
            if suffix.isdecimal() and (candidate/'.glomap-ready').is_file():
                return candidate
    return base


def environment(root, *, install_prefix=None):
    base = install_prefix if install_prefix is not None else prefix(root)
    env = os.environ.copy()
    managed = install_prefix is not None or (base/'.glomap-ready').is_file()
    if managed:
        paths = [base/'Library/bin', base/'Library/mingw-w64/bin', base/'Library/usr/bin',
                 base/'Scripts', base] if target() == 'win-64' else [base/'bin']
        env['PATH'] = os.pathsep.join([*map(str, paths), env.get('PATH', '')])
        # Qt must not mix plugins from the bundled COLMAP with conda's Qt.
        env.pop('QT_PLUGIN_PATH', None)
        env.pop('QT_QPA_PLATFORM_PLUGIN_PATH', None)
        plugins = base/('Library/plugins' if target() == 'win-64' else 'plugins')
        if plugins.is_dir():
            env['QT_PLUGIN_PATH'] = str(plugins)
    bundled = bundled_tools()
    if bundled and not managed:
        paths = sorted({str(p.parent) for p in bundled.rglob('*.dll')} |
                       {str(p.parent) for p in bundled.rglob('*.exe')})
        env['PATH'] = os.pathsep.join([*paths, env.get('PATH', '')])
        plugins = sorted((bundled/'colmap').rglob('plugins'))
        if plugins:
            env['QT_PLUGIN_PATH'] = os.pathsep.join(map(str, plugins))
            env.pop('QT_QPA_PLATFORM_PLUGIN_PATH', None)
    env['LC_ALL'] = 'C'
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    return env


def resolve_tool(name, value, root):
    value = os.path.expanduser(value.strip()) or name
    folder = Path(value)
    base = prefix(root)
    if value == name and (base/'.glomap-ready').is_file():
        windows = target() == 'win-64'
        path = base/('Library/bin' if windows else 'bin')/(name + ('.exe' if windows else ''))
        if not path.is_file():
            raise ValueError(f'Installed {name.upper()} missing at {path}. Run Install / Repair Tools.')
        return str(path)
    if value == name and bundled_tools():
        return str(bundled_executable(name))
    if value != name and folder.is_dir():
        filename = f'{name}.exe' if os.name == 'nt' else name
        locations = [Path('.'), Path('bin'), Path('Library/bin')]
        if name == 'colmap':
            locations += [Path('build/src/colmap/exe'), Path('src/colmap/exe')]
        for location in locations:
            path = shutil.which(str(folder.absolute()/location/filename))
            if path:
                return path
        raise ValueError(f'No runnable {filename} found in {folder}. Select the executable itself under Setup.')
    path = shutil.which(value) or (shutil.which(name, path=environment(root)['PATH']) if value == name else None)
    if not path:
        raise ValueError(f'{name.upper()} executable missing. Install Tools or select its path under Setup.')
    if os.name == 'nt' and Path(path).suffix.lower() != '.exe':
        raise ValueError(f'Select the {name}.exe executable on Windows')
    return str(Path(path).absolute())


def install(job, root, log_dir, *, allow_download=True):
    log_dir.mkdir(parents=True, exist_ok=True)
    status = log_dir/'setup-diagnostics.log'
    status.write_text(f'Platform: {platform.platform()}\nLogs: {log_dir.resolve()}\n', encoding='utf-8')
    try:
        if bundled_tools() and not (prefix(root)/'.glomap-ready').is_file():
            tools = {name: str(bundled_executable(name)) for name in CHECKS}
            runner = Pipeline(job, root, {}, tools, environment(root), log_dir)
            try:
                for i, (name, args) in enumerate(CHECKS.items(), 1):
                    job.message(f'Verifying bundled {name.upper()}')
                    job.install_step = (4, i - 1, len(CHECKS))
                    runner.run_command(name, args, log_dir/f'bundled-check-{name}.log')
            except WindowsLoaderError as exc:
                with status.open('a', encoding='utf-8') as stream:
                    stream.write(f'Bundled tool loader failure: {exc}\n')
                job.check_cancelled()
                if not allow_download:
                    raise RuntimeError('Bundled tools need repair. Enable Allow Online Access in '
                                       'Blender Preferences, then click Install / Repair Tools.') from exc
                job.message('Repairing tools with a separate conda-forge environment')
                job.add_output(['Bundled loader failure; downloading a complete CPU tool environment.',
                                'Original failure retained in bundled-check-*.log.'], None)
                result = install_environment(job, root, log_dir)
            else:
                job.install_step = (4, len(CHECKS), len(CHECKS))
                job.message('Bundled COLMAP, FFmpeg and FFprobe verified')
                result = 0
        else:
            if not allow_download and not (prefix(root)/'.glomap-ready').is_file():
                raise RuntimeError('Enable Allow Online Access in Blender Preferences to download tools.')
            result = install_environment(job, root, log_dir)
    except Exception as exc:
        with status.open('a', encoding='utf-8') as stream:
            stream.write(f'Failed: {type(exc).__name__}: {exc}\n')
        raise
    with status.open('a', encoding='utf-8') as stream:
        stream.write('All executable checks passed.\n')
    return result


def install_environment(job, root, log_dir):
    subdir = target()
    if subdir not in BOOTSTRAP:
        raise RuntimeError(f'No automatic tool install for {platform.system()} {platform.machine()}. '
                           'Select compatible executables under Setup.')
    lock = root/'.install-lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(f'Tool installation is locked at {lock}') from exc
    creating_env = False
    try:
        job.add_output([f'Setup logs: {log_dir.resolve()}'], None)
        dest = prefix(root)
        if not (dest/'.glomap-ready').is_file():
            original_dest = dest
            suffix = 1
            # Micromamba requires a fresh target. Preserve any existing folder or file.
            while os.path.lexists(dest):
                suffix += 1
                dest = original_dest.with_name(f'{original_dest.name}-{suffix}')
            if dest != original_dest:
                job.add_output([f'Existing tool folder left in place: {original_dest}',
                                f'Installing into a new folder: {dest}'], None)
            with tempfile.TemporaryDirectory(dir=root, ignore_cleanup_errors=True) as tmp:
                folder = Path(tmp)
                archive = folder/'micromamba.tar.bz2'
                url = f'https://conda.anaconda.org/conda-forge/{subdir}/micromamba-2.6.2-1.tar.bz2'
                job.install_step = (1, 0, 0)
                job.message(f'Downloading Micromamba for {subdir}')
                job.add_output([f'Download source: {url}', f'Tool environment: {dest}'], None)
                digest = hashlib.sha256()
                with urllib.request.urlopen(url, timeout=20) as response, archive.open('wb') as output:
                    size = response.headers.get('Content-Length', '')
                    total = int(size) if size.isdecimal() else 0
                    while chunk := response.read(256 * 1024):
                        job.check_cancelled()
                        output.write(chunk)
                        digest.update(chunk)
                        job.install_step = (1, output.tell(), total)
                if digest.hexdigest() != BOOTSTRAP[subdir]:
                    raise RuntimeError('Micromamba download failed SHA-256 verification')
                job.install_step = (2, 0, 0)
                job.message('Checking Micromamba')
                exe = folder/('micromamba.exe' if os.name == 'nt' else 'micromamba')
                with tarfile.open(archive, 'r:bz2') as bundle, exe.open('wb') as output:
                    with bundle.extractfile('Library/bin/micromamba.exe' if os.name == 'nt' else 'bin/micromamba') as member:
                        shutil.copyfileobj(member, output)
                exe.chmod(0o700)
                prepare_bootstrap_runtime(folder)
                runner = Pipeline(job, root, {}, {'micromamba': str(exe)}, os.environ.copy(), log_dir)
                runner.run_command('micromamba', ['--version'], log_dir/'micromamba-version.log')
                job.install_step = (3, 0, 0)
                job.message('Resolving packages')
                package_total = linked = 0
                download_size = ''

                def package_progress(lines):
                    nonlocal package_total, linked, download_size
                    for line in lines:
                        line = line.strip()
                        match = re.fullmatch(r'Install: (\d+) packages?', line)
                        if match:
                            package_total = int(match[1])
                        elif line.startswith('Total download:'):
                            download_size = line.partition(':')[2].strip()
                        elif line == 'Transaction starting':
                            job.message(f'Preparing package downloads ({download_size})' if download_size
                                        else 'Preparing package downloads')
                        elif line.startswith('Linking '):
                            if not linked:
                                job.message('Linking packages into the tool environment')
                            job.install_step = (3, linked, package_total)
                            linked += 1

                dest.parent.mkdir(exist_ok=True)
                creating_env = True
                runner.run_command('micromamba', ['--no-rc', '-r', root, 'create', '-y', '-p', dest,
                                   '--override-channels', '-c', 'https://conda.anaconda.org/conda-forge',
                                   f'colmap={COLMAP_VERSION}=cpu*', 'ffmpeg>=7.1'], log_dir/'install.log',
                                   on_output=package_progress)
        bin_dir = dest/('Library/bin' if os.name == 'nt' else 'bin')
        tools = {name: str(bin_dir/(name + ('.exe' if os.name == 'nt' else ''))) for name in CHECKS}
        runner = Pipeline(job, root, {}, tools, environment(root, install_prefix=dest), log_dir)
        job.install_step = (4, 0, len(CHECKS))
        for i, (name, args) in enumerate(CHECKS.items(), 1):
            job.message(f'Verifying {name.upper()}')
            if not Path(tools[name]).is_file():
                hint = ' Check Windows Security > Protection history if the file was removed.' if os.name == 'nt' else ''
                raise RuntimeError(f'Installed executable missing at {tools[name]}.{hint}')
            runner.run_command(name, args, log_dir/f'install-check-{name}.log')
            job.install_step = (4, i, len(CHECKS))
        job.check_cancelled()
        (dest/'.glomap-ready').touch()
        job.message('COLMAP, FFmpeg and FFprobe verified')
        return 0
    except Exception:
        job.add_output([f'Setup failed. Logs retained in: {log_dir.resolve()}'], None)
        if creating_env and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        lock.rmdir()
