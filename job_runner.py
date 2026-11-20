"""Background jobs independent of Blender’s Python API."""
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import shutil
import tempfile
from threading import Condition, Event, Lock, Thread
from time import monotonic

from .reconstruction import Cancelled, Pipeline, WORK_DIR

VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mts', '.m2ts', '.mpg', '.mpeg'}
PAUSE_SUPPORTED = os.name == 'posix'
TERMINAL_CLIP_STATUSES = {'Finished', 'Already finished', 'Failed', 'Cancelled', 'Not started'}
LEGACY_WORK_DIR = '04 SCENES'
DEFAULTS = {
    'MAX_IMAGE_SIZE': '2000', 'MAX_FEATURES': '8192', 'THREADS': '4',
    'OVERLAP': '10', 'QUADRATIC_OVERLAP': '1', 'GPU_MODE': 'auto',
    'GPU_INDEX': '0', 'MAPPER': 'incremental', 'CAMERA_MODEL': 'SIMPLE_RADIAL',
    'SINGLE_CAMERA': '1', 'MIN_REGISTERED': '10', 'MIN_REGISTERED_PERCENT': '100',
    'MIN_POINTS': '100', 'MAX_REPROJECTION_ERROR': '2.0',
}


def project_path(value):
    if not value or not value.strip():
        raise ValueError('Choose an output/project folder first')
    path = Path(value).expanduser().resolve()
    if path == Path(path.anchor):
        raise ValueError('Choose a project folder, not the filesystem root')
    return path


def workspace_path(destination):
    return project_path(destination) / 'glomap'


def validate_videos(videos):
    if not videos:
        raise ValueError('Add a clip with + in Video List to process')
    sources = []
    names = {}
    for value in videos:
        source = Path(value).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in VIDEO_SUFFIXES:
            raise ValueError(f'Unsupported video: {source.name}')
        if any(ord(c) < 32 for c in source.name):
            raise ValueError('Video names cannot contain control characters')
        key = source.name.casefold()
        if key in names and names[key] != source:
            raise ValueError(f'Two selected videos have the same filename: {source.name}')
        if key not in names:
            sources.append(source)
        names[key] = source
    return sources


def copy_input_clip(source, folder, check_cancelled):
    """Publish a complete copy, keeping any file already using the same name."""
    if source.parent == folder:
        return source
    with tempfile.NamedTemporaryFile(dir=folder, prefix='.clip-', suffix='.part', delete=False) as stream:
        temp = Path(stream.name)
        try:
            with source.open('rb') as video:
                while chunk := video.read(1024 * 1024):
                    check_cancelled()
                    stream.write(chunk)
            stream.close()
            check_cancelled()
            number = 1
            while True:
                name = source.name if number == 1 else f'{source.stem} ({number}){source.suffix}'
                target = folder / name
                try:
                    target.touch(exist_ok=False)
                except FileExistsError:
                    number += 1
                    continue
                try:
                    temp.replace(target)
                except OSError:
                    target.unlink(missing_ok=True)
                    raise
                return target
        finally:
            stream.close()
            temp.unlink(missing_ok=True)


STAGES = {
    'start': 'Preparing',
    'probe': 'Reading video metadata',
    'frames': 'Extracting frames',
    'features': 'Extracting features',
    'matching': 'Matching frames',
    'calibrate': 'Calibrating cameras',
    'global': 'Global mapping',
    'incremental': 'Incremental mapping',
    'analyze': 'Checking reconstruction',
    'undistort': 'Correcting distortion',
    'export-txt': 'Exporting model',
    'final-analysis': 'Validating export',
    'copy': 'Saving results',
}


@dataclass
class ClipProgress:
    name: str
    status: str = 'Queued'
    stage: str = ''


class Job:
    def __init__(self):
        self.thread = None
        self.stop = Event()
        self.pause_condition = Condition()
        self.paused = False
        self.paused_clips = set()
        self.waiting_for_clips = False
        self.events = SimpleQueue()
        self.root = self.log_path = None
        self.clips, self.results = [], []
        self.returncode = None
        self.cancelled = False
        self.status = 'Ready'
        self.console_lock = Lock()
        self.console = deque(maxlen=100)
        self.command_started = self.last_output = None
        self.command_log = None
        self.counter = None
        self.install_step = None

    def begin_command(self, command, log):
        with self.console_lock:
            self.command_started = monotonic()
            self.last_output = None
            self.command_log = log
            self.counter = None
            self.console.append('$ ' + repr(command))

    def add_output(self, lines, counter):
        with self.console_lock:
            if lines:
                self.console.extend(lines)
                self.last_output = monotonic()
            if counter is not None:
                self.counter = counter

    def console_snapshot(self):
        with self.console_lock:
            return tuple(self.console), self.command_started, self.last_output, self.counter

    @property
    def running(self):
        return self.thread is not None and self.returncode is None

    def check_cancelled(self):
        if self.stop.is_set():
            raise Cancelled()

    def is_paused(self, index=None):
        with self.pause_condition:
            return self.paused or index in self.paused_clips

    def toggle_pause(self, index=None):
        with self.pause_condition:
            if index is None:
                self.paused = not self.paused
            elif index in self.paused_clips:
                self.paused_clips.remove(index)
            else:
                self.paused_clips.add(index)
            self.pause_condition.notify_all()

    def wait_until_resumed(self, index=None):
        with self.pause_condition:
            self.pause_condition.wait_for(
                lambda: self.stop.is_set() or not (self.paused or index in self.paused_clips))
        self.check_cancelled()

    def next_clip(self, pending):
        with self.pause_condition:
            try:
                while True:
                    self.check_cancelled()
                    if not self.paused:
                        for index in pending:
                            if index not in self.paused_clips:
                                return index
                        self.waiting_for_clips = True
                    self.pause_condition.wait()
            finally:
                self.waiting_for_clips = False

    def event(self, index, stage, result=None):
        self.events.put((index, stage, result))

    def message(self, message):
        self.events.put((None, message, None))

    def launch(self, target, log, clips=()):
        if self.running:
            raise ValueError('A job is already running')
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f'Job started. Logs: {log.parent.resolve()}\n', encoding='utf-8')
        self.stop.clear()
        self.paused = False
        self.paused_clips.clear()
        self.waiting_for_clips = False
        self.returncode = None
        self.cancelled = False
        self.results = []
        self.clips = [ClipProgress(p.name) for p in clips]
        self.log_path = log
        self.status = 'Starting'
        self.install_step = None
        with self.console_lock:
            self.console.clear()
            self.command_started = self.last_output = self.counter = None
            self.command_log = None

        def worker():
            code = 1
            try:
                code = target()
            except Cancelled:
                code = 130
            except Exception as exc:
                if log is not None:
                    try:
                        with log.open('a', encoding='utf-8') as stream:
                            print(f'{type(exc).__name__}: {exc}', file=stream)
                    except OSError:
                        pass
                self.message(str(exc))
            finally:
                self.events.put((None, None, code))

        self.thread = Thread(target=worker, daemon=True)
        self.thread.start()

    def start(self, root, sources, settings, tools, env, output_folder=None):
        self.root = project_path(str(root))
        output = (output_folder or self.root/'finished_tracks').resolve()
        work, failed = self.root/WORK_DIR, self.root/'failed'
        legacy = self.root/LEGACY_WORK_DIR
        paths = [work, failed]
        reserved = paths + ([legacy] if legacy.exists() else [])
        if output == self.root or any(output.is_relative_to(path) or path.is_relative_to(output) for path in reserved):
            raise ValueError('Output Folder is for finished results. Clear it to use finished_tracks, '
                             'or choose a separate folder outside output_tracks and failed.')
        if any(path.resolve() != path for path in paths):
            raise ValueError('Project subfolders must be regular directories')
        if legacy.is_dir() and not legacy.is_symlink() and legacy.resolve() == legacy and not work.exists():
            guard = legacy/'.track.lock'
            try:
                guard.mkdir()
            except FileExistsError as exc:
                raise ValueError(f'Cannot rename the old working folder while it is locked: {guard}') from exc
            try:
                legacy.rename(work)
                guard = work/guard.name
            finally:
                guard.rmdir()
        for path in [*paths, output]:
            path.mkdir(parents=True, exist_ok=True)
        log_dir = Path(tempfile.mkdtemp(prefix='.addon-job-', dir=work))
        lock = work/'.track.lock'

        def run():
            try:
                lock.mkdir()
            except FileExistsError as exc:
                raise RuntimeError(f'Project is locked: {lock}') from exc
            try:
                return Pipeline(self, self.root, DEFAULTS | {k:str(v) for k,v in settings.items()},
                                tools, env, log_dir).batch(sources, output)
            finally:
                lock.rmdir()

        self.launch(run, log_dir/'batch.log', sources)

    def poll(self):
        while True:
            try:
                index, stage, value = self.events.get_nowait()
            except Empty:
                break
            if self.log_path:
                try:
                    with self.log_path.open('a', encoding='utf-8') as log:
                        print(index if index is not None else '', stage if stage is not None else value, file=log)
                except OSError:
                    pass
            if stage is None:
                self.returncode = value
                if self.cancelled:
                    self.status = 'Cancelled'
                elif value == 0:
                    self.status = f'Finished: {len(self.results)} clip(s)' if self.clips else self.status
                else:
                    self.status = f'Failed: {self.status}'
                for clip in self.clips:
                    if clip.status == 'Queued':
                        clip.status = 'Not started'
                    elif clip.status not in TERMINAL_CLIP_STATUSES:
                        clip.status = 'Cancelled' if self.cancelled else 'Failed'
            elif index is None:
                self.status = stage
            else:
                clip = self.clips[index]
                if stage in ('finished', 'skipped'):
                    clip.status = 'Finished' if stage == 'finished' else 'Already finished'
                    self.results.append(value)
                elif stage == 'failed':
                    clip.status = 'Failed'
                else:
                    clip.status = STAGES[stage]
                    clip.stage = stage
                    self.status = f'{clip.name}: {clip.status}'
        return self.running

    def cancel(self):
        if self.running:
            self.cancelled = True
            self.status = 'Cancelling'
            with self.pause_condition:
                self.stop.set()
                self.pause_condition.notify_all()

    def close(self):
        if self.running:
            self.cancel()
            self.thread.join(timeout=5)
            self.poll()
