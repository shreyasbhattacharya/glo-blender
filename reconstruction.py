"""Reconstruct clips with the COLMAP CLI and preserve the existing project layout."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile


BINARY = ('cameras.bin', 'images.bin', 'points3D.bin')
TEXT = ('cameras.txt', 'images.txt', 'points3D.txt')
WORK_DIR = 'output_tracks'

# COLMAP logs extraction after completion and matching/undistortion before work.
IMAGE_PROGRESS = {
    'features': (re.compile(r'Processed file \[(\d+)\s*/\s*(\d+)\]'), False),
    'matching': (re.compile(r'Processing image \[(\d+)\s*/\s*(\d+)\]'), True),
    'undistort': (re.compile(r'Undistorting image \[(\d+)\s*/\s*(\d+)\]'), True),
}
FRAME_PROGRESS = re.compile(r'^frame\s*=\s*(\d+)\b')


def stage_counter(stage, line, frame_total):
    if stage == 'frames':
        match = FRAME_PROGRESS.match(line.lstrip())
        if match:
            count = int(match[1])
            return count, frame_total if frame_total and count <= frame_total else None, 'frames', False
    elif stage in IMAGE_PROGRESS:
        pattern, in_progress = IMAGE_PROGRESS[stage]
        match = pattern.search(line)
        if match:
            current, total = map(int, match.groups())
            if 0 < current <= total:
                return current, total, 'images', in_progress
    return None


class Cancelled(Exception):
    pass


def windows_error_hint(code):
    if code in (225, 226, 0xC0000906, 0xC0000907):
        return 'Windows antivirus blocked or removed the file. See Windows Security > Protection history.'
    if code in (577, 1260, 0xC0000428):
        return 'Windows could not verify or allow the file. Check App & browser control or ask your administrator.'
    if code == 5:
        return 'Windows denied access. Check file permissions and Windows Security.'
    if code == 0xC0000135:
        return 'A required DLL could not be loaded.'
    if code in (193, 0xC000007B):
        return 'The executable or one of its DLLs has an incompatible format.'
    return ''


def stop_command(child):
    if os.name == 'nt':
        taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'taskkill.exe'
        try:
            subprocess.run([str(taskkill), '/PID', str(child.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
            os.killpg(child.pid, signal.SIGCONT)
            child.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        # A launcher can exit in response to SIGTERM while descendants are still alive.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if child.poll() is None:
        child.kill()
    child.wait()


class Pipeline:
    def __init__(self, job, root, settings, tools, env, log_dir):
        self.job, self.root, self.settings = job, root, settings
        self.tools, self.env, self.log_dir = tools, env, log_dir
        self.flags = {}

    def run_command(self, tool, args, log, stage=None, *, on_output=None):
        index = getattr(self, 'index', None)
        self.job.wait_until_resumed(index)
        with log.open('w', encoding='utf-8') as stream:
            command = [self.tools[tool], *map(str, args)]
            stream.write(repr(command) + '\n')
            stream.flush()
            self.job.begin_command(command, log)

            def fail(message):
                stream.write(message + '\n')
                self.job.add_output([message], None)
                return RuntimeError(message)

            try:
                child = subprocess.Popen(command, env=self.env, stdin=subprocess.DEVNULL,
                                         stdout=stream, stderr=subprocess.STDOUT,
                                         start_new_session=os.name != 'nt',
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as exc:
                hint = windows_error_hint(getattr(exc, 'winerror', None))
                raise fail(f'Could not start {command[0]}: {exc}' + (f' {hint}' if hint else '')) from exc

            # Read the existing log incrementally. The child never waits for a UI reader.
            with child, log.open('rb') as live:
                live.readline()  # The command is already in the console.
                pending = b''

                def drain(final=False):
                    nonlocal pending
                    while True:
                        data = live.read(65536)
                        lines = re.split(rb'[\r\n]+', pending + data)
                        pending = b'' if final and not data else lines.pop()
                        records = [line.decode('utf-8', errors='replace') for line in lines if line.strip()]
                        if records and on_output:
                            on_output(records)
                        counter = None
                        if stage:
                            for line in records:
                                progress = stage_counter(stage, line, getattr(self, 'frame_total', None))
                                if progress:
                                    counter = (self.index, stage, *progress)
                        self.job.add_output(records, counter)
                        if not final or not data:
                            break

                while True:
                    drain()
                    if self.job.stop.wait(0.15):
                        stop_command(child)
                        drain(final=True)
                        raise Cancelled()
                    code = child.poll()
                    if code is not None:
                        break
                    if os.name == 'posix' and self.job.is_paused(index):
                        try:
                            os.killpg(child.pid, signal.SIGSTOP)
                            self.job.wait_until_resumed(index)
                            os.killpg(child.pid, signal.SIGCONT)
                        except ProcessLookupError:
                            pass
                        except Cancelled:
                            stop_command(child)
                            drain(final=True)
                            raise
                drain(final=True)
                if code:
                    status = code & 0xFFFFFFFF
                    hint = windows_error_hint(status) if os.name == 'nt' and status >= 0x80000000 else ''
                    if hint:
                        raise fail(f'{command[0]} exited with Windows status 0x{status:08X}. {hint} See {log.name}.')
                    raise fail(f'{tool} exited with code {code}. See {log.name}.')
        return log

    def preflight(self):
        help_log = self.run_command('colmap', ['help'], self.log_dir/'colmap-help.log')
        self.run_command('ffmpeg', ['-version'], self.log_dir/'ffmpeg-version.log')
        help_text = help_log.read_text(errors='replace')
        commands = ['feature_extractor', 'sequential_matcher', 'mapper', 'model_analyzer',
                    'model_converter', 'image_undistorter']
        global_cmds = ('global_mapper', 'view_graph_calibrator')
        available = all(re.search(r'\b'+cmd+r'\b', help_text) for cmd in global_cmds)
        if self.settings['MAPPER'] == 'global' and not available:
            raise RuntimeError('This COLMAP build lacks the global mapper or view graph calibrator')
        self.use_global = available and self.settings['MAPPER'] != 'incremental'
        if self.use_global:
            commands.extend(global_cmds)
        for cmd in commands:
            log = self.run_command('colmap', [cmd, '--help'], self.log_dir/f'{cmd}.help.log')
            self.flags[cmd] = set(re.findall(r'--([\w.]+)', log.read_text(errors='replace')))
        self.gpu = self.settings['GPU_MODE'] == 'gpu' or (
            self.settings['GPU_MODE'] == 'auto'
            and bool(re.search(r'with CUDA|CUDA enabled|CUDA_ENABLED', help_text, re.I)))
        if self.settings['GPU_MODE'] == 'auto' and shutil.which('nvidia-smi'):
            self.gpu = True

    def step(self, label, tool, *args):
        self.job.event(self.index, label)
        self.step_no += 1
        log = self.scene/'logs'/f'{self.step_no:03d}-{label}.log'
        return self.run_command(tool, args, log, stage=label)

    def declared_frame_count(self, source):
        if 'ffprobe' not in self.tools:
            return None
        try:
            log = self.step('probe', 'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=nb_frames', '-of', 'default=nw=1:nk=1', source)
            value = log.read_text(encoding='utf-8', errors='replace').splitlines()[-1].strip()
            return int(value) if value.isdecimal() and int(value) > 0 else None
        except (OSError, RuntimeError):
            return None

    def colmap(self, label, cmd, required, optional=None):
        flags = self.flags[cmd]
        missing = required.keys() - flags
        if missing:
            raise RuntimeError(f'{cmd} does not support {", ".join(sorted(missing))}')
        options = dict(required)
        options.update({k:v for k,v in (optional or {}).items() if k in flags})
        args = [arg for key, value in options.items() for arg in (f'--{key}', value)]
        return self.step(label, 'colmap', cmd, *args)

    def features(self, matching):
        s = self.settings
        cmd = 'sequential_matcher' if matching else 'feature_extractor'
        legacy, modern = ('SiftMatching', 'FeatureMatching') if matching else ('SiftExtraction', 'FeatureExtraction')
        required = {'database_path': self.scene/'database.db'}
        for suffix, value in [('use_gpu', int(self.gpu)), ('gpu_index', s['GPU_INDEX']), ('num_threads', s['THREADS'])]:
            key = f'{modern}.{suffix}'
            required[key if key in self.flags[cmd] else f'{legacy}.{suffix}'] = value
        if matching:
            required.update({'SequentialMatching.overlap': s['OVERLAP'],
                             'SequentialMatching.quadratic_overlap': s['QUADRATIC_OVERLAP'],
                             'SequentialMatching.loop_detection': 0})
        else:
            size_key = 'FeatureExtraction.max_image_size'
            required.update({'image_path': self.scene/'images', 'ImageReader.single_camera': s['SINGLE_CAMERA'],
                             'ImageReader.camera_model': s['CAMERA_MODEL'], 'SiftExtraction.max_num_features': s['MAX_FEATURES'],
                             size_key if size_key in self.flags[cmd] else 'SiftExtraction.max_image_size': s['MAX_IMAGE_SIZE']})
        self.colmap('matching' if matching else 'features', cmd, required)

    def metrics(self, path, label='analyze'):
        log = self.colmap(label, 'model_analyzer', {'path': path})
        data = log.read_text(errors='replace')
        found = [re.search(pattern, data) for pattern in (
            r'Registered images:\s*(\d+)', r'\bPoints:\s*(\d+)', r'Mean reprojection error:\s*(\d+(?:\.\d+)?)\s*px')]
        if not all(found):
            raise RuntimeError(f'Could not read reconstruction metrics from {log.name}')
        count, points, error = (m[1] for m in found)
        return int(count), int(points), float(error), log

    def verify(self, path, label='analyze'):
        count, points, error, log = self.metrics(path, label)
        s = self.settings
        if count != self.frames or points < int(s['MIN_POINTS']) or error > float(s['MAX_REPROJECTION_ERROR']):
            raise RuntimeError(f'Quality check failed: {count}/{self.frames} poses, {points} points, {error} px')
        return log

    def map(self, global_mapping):
        label = 'global' if global_mapping else 'incremental'
        folder = self.scene/label
        folder.mkdir()
        db = self.scene/'database.db'
        if global_mapping:
            db = Path(shutil.copy2(db, self.scene/'global.db'))
            self.colmap('calibrate', 'view_graph_calibrator', {'database_path': db})
        options = ({'GlobalMapper.ba_use_gpu': 0, 'GlobalMapper.ba_ceres_use_gpu': 0,
                    'GlobalMapper.gp_use_gpu': 0, 'GlobalMapper.num_threads': self.settings['THREADS']}
                   if global_mapping else {'Mapper.ba_use_gpu': 0, 'Mapper.num_threads': self.settings['THREADS']})
        self.colmap(label, 'global_mapper' if global_mapping else 'mapper',
                    {'database_path': db, 'image_path': self.scene/'images', 'output_path': folder}, options)
        problems = []
        for model in [folder, *sorted(p for p in folder.iterdir() if p.is_dir())]:
            if not all((model/name).is_file() for name in BINARY):
                continue
            try:
                self.verify(model)
                return model
            except RuntimeError as exc:
                problems.append(str(exc))
        raise RuntimeError(problems[-1] if problems else 'COLMAP produced no sparse model')

    def process(self, source, dest):
        scene, s = self.scene, self.settings
        (scene/'images').mkdir(parents=True)
        (scene/'logs').mkdir()
        (scene/'.incomplete').touch()
        info = source.stat()
        signature = json.dumps({'source': str(source), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                                'settings': s, 'format': 1}, sort_keys=True)
        size = s['MAX_IMAGE_SIZE']
        self.frame_total = self.declared_frame_count(source)
        self.step('frames', 'ffmpeg', '-hide_banner', '-nostdin', '-stats', '-n', '-xerror', '-i', source,
                  '-map', '0:v:0', '-an', '-sn', '-dn', '-vf',
                  f"scale=w='min(iw,{size})':h='min(ih,{size})':force_original_aspect_ratio=decrease",
                  '-fps_mode', 'passthrough', '-q:v', 2, '-threads', s['THREADS'], scene/'images'/'frame_%06d.jpg')
        self.frames = len(list((scene/'images').glob('frame_*.jpg')))
        if self.frames < int(s['MIN_REGISTERED']):
            raise RuntimeError(f'Too few extracted frames: {self.frames}')
        for matching in (False, True):
            try:
                self.features(matching)
            except RuntimeError:
                if not self.gpu:
                    raise
                self.gpu = False
                self.job.message('GPU attempt failed. Retrying on CPU.')
                if not matching:
                    archive = scene/'gpu-failed'
                    archive.mkdir()
                    for file in scene.glob('database.db*'):
                        file.rename(archive/file.name)
                self.features(matching)
        if self.use_global:
            try:
                model = self.map(True)
            except RuntimeError as exc:
                self.job.message(f'Global mapping failed: {exc}. Trying incremental mapping.')
                model = self.map(False)
        else:
            model = self.map(False)
        stage = Path(tempfile.mkdtemp(prefix='.partial-', dir=dest.parent))
        try:
            self.colmap('undistort', 'image_undistorter', {'image_path': scene/'images', 'input_path': model,
                        'output_path': stage, 'output_type': 'COLMAP', 'max_image_size': size})
            sparse = stage/'sparse'
            output = sparse/'0'
            output.mkdir(exist_ok=True)
            for file in sparse.glob('*.bin'):
                file.rename(output/file.name)
            self.colmap('export-txt', 'model_converter', {'input_path': output, 'output_path': output, 'output_type': 'TXT'})
            analysis = self.verify(output, 'final-analysis')
            self.job.event(self.index, 'copy')
            self.job.check_cancelled()
            if not all((output/name).is_file() and (output/name).stat().st_size for name in BINARY + TEXT):
                raise RuntimeError('Export contains missing or empty model files')
            cameras = (output/'cameras.txt').read_text(encoding='utf-8').splitlines()
            if any(line.split()[1] not in ('PINHOLE', 'SIMPLE_PINHOLE')
                   for line in cameras if line.strip() and not line.startswith('#')):
                raise RuntimeError('Expected undistorted pinhole cameras')
            names = (output/'images.txt').read_text(encoding='utf-8').splitlines()
            poses = [line.split(maxsplit=9)[-1] for line in [x for x in names if not x.startswith('#')][::2] if line.strip()]
            if len(set(poses)) != self.frames or not all((stage/'images'/name).is_file() for name in poses):
                raise RuntimeError('Exported poses do not match the corrected images')
            if source.stat().st_mtime_ns != info.st_mtime_ns or source.stat().st_size != info.st_size:
                raise RuntimeError('Source video changed during reconstruction')
            shutil.copy2(analysis, stage/'model-analysis.txt')
            shutil.copytree(scene/'logs', stage/'logs')
            (stage/'frame-count.txt').write_text(f'Extracted frames: {self.frames}\nRegistered frames: {self.frames}/{self.frames}\n')
            (stage/'source-and-settings.txt').write_text(f'source={source}\n{signature}\n', encoding='utf-8')
            (stage/'.done').write_text(signature, encoding='utf-8')
            self.job.wait_until_resumed(self.index)
            if os.path.lexists(dest):
                raise RuntimeError(f'Output already exists: {dest}')
            stage.rename(dest)
            (scene/'.incomplete').unlink(missing_ok=True)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def batch(self, sources, output):
        self.preflight()
        failed = False
        pending = list(range(len(sources)))
        while pending:
            self.index = self.job.next_clip(pending)
            pending.remove(self.index)
            source = sources[self.index]
            self.job.event(self.index, 'start')
            self.scene = self.root/WORK_DIR/source.name
            dest = output/source.name
            self.step_no = 0
            try:
                if os.path.lexists(dest):
                    marker = dest/'.done'
                    if not marker.is_file() or not all((dest/'sparse'/'0'/f).is_file() for f in BINARY+TEXT):
                        raise RuntimeError(f'Output already exists and is incomplete: {dest}')
                    metadata = marker.read_text(encoding='utf-8')
                    if not metadata.startswith('{'):
                        legacy = re.search(r'^source=(.+)$', metadata, flags=re.M)
                        size = re.search(r'^bytes=(\d+)', metadata, flags=re.M)
                        if (not metadata.startswith('track-format=2-all-frames\n') or not legacy or not size
                                or Path(legacy[1]).resolve() != source or int(size[1]) != source.stat().st_size):
                            raise RuntimeError(f'Existing output is from a different source or format: {dest}')
                    if metadata.startswith('{'):
                        previous = json.loads(metadata)
                        if (previous['source'], previous['size'], previous['mtime_ns'], previous['settings']) != (
                                str(source), source.stat().st_size, source.stat().st_mtime_ns, self.settings):
                            raise RuntimeError(f'Output exists with different source or settings: {dest}')
                    self.job.event(self.index, 'skipped', dest)
                    continue
                if self.scene.exists():
                    self.archive()
                self.process(source, dest)
                self.job.event(self.index, 'finished', dest)
            except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
                failed = True
                self.job.event(self.index, 'failed')
                self.job.message(str(exc))
                if (self.scene/'.incomplete').exists():
                    self.archive()
        return int(failed)

    def archive(self):
        archive = Path(tempfile.mkdtemp(prefix='scene-', dir=self.root/'failed'))
        relocated = self.scene.rename(archive/'scene')
        log = self.job.command_log
        if log and log.is_relative_to(self.scene):
            self.job.command_log = relocated / log.relative_to(self.scene)
