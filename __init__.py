"""A Blender sidebar front end for the all-frame COLMAP/GLOMAP pipeline."""
bl_info = {
    'name': 'Video to 3D — Blender GLOMAP',
    'author': 'blender-glomap contributors',
    'version': (0, 3, 13),
    'blender': (4, 0, 0),
    'location': '3D View > Sidebar > Video to 3D',
    'description': 'Track videos, reconstruct sparse scenes, and import each clip into its own collection',
    'category': 'Import-Export',
}

import atexit
import os
from pathlib import Path
import textwrap
from time import monotonic
import traceback

import bpy
import bpy.utils.previews
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ImportHelper

from . import import_bridge, job_runner as jobs, toolchain as tool_setup
from .reconstruction import windows_error_hint

_JOB = jobs.Job()
_SETUP = jobs.Job()
_INPUT = jobs.Job()
_INPUT_SCENE = None
_TOOL_RESULTS = {}
_TOOL_NAMES = ('colmap', 'ffmpeg', 'ffprobe')
_INSTALLING = False
_IMPORTER_ERROR = ''
_ICONS = None
_JOB_SCENE = None
_POST_IMPORT = False


def preferences():
    return bpy.context.preferences.addons[__package__].preferences


def abs_path(value):
    return bpy.path.abspath(value) if value else ''


def tool_root():
    return Path(bpy.utils.user_resource('DATAFILES', path='blender_glomap/tools'))


def find_tool(name):
    return tool_setup.resolve_tool(name, abs_path(getattr(preferences(), name)), tool_root())


def probe_path():
    try:
        return find_tool('ffprobe')
    except ValueError:
        return ''


def tick_setup():
    running = _SETUP.poll()
    redraw()
    return 0.3 if running else None


def tick_input():
    global _INPUT_SCENE
    running = _INPUT.poll()
    if not running and _INPUT_SCENE:
        if _INPUT_SCENE.name in bpy.data.scenes:
            settings = _INPUT_SCENE.glomap
            existing = {item.filepath for item in settings.videos}
            for path in _INPUT.results:
                if str(path) not in existing:
                    settings.videos.add().filepath = str(path)
                    existing.add(str(path))
            if settings.videos:
                settings.active_video = len(settings.videos) - 1
            settings.message = _INPUT.status
        _INPUT_SCENE = None
    redraw()
    return 0.25 if running else None


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def tick():
    global _POST_IMPORT
    try:
        running = _JOB.poll()
        if not running and not _POST_IMPORT:
            _POST_IMPORT = True
            if _JOB_SCENE and _JOB_SCENE.name in bpy.data.scenes:
                settings = _JOB_SCENE.glomap
                if _JOB.results:
                    settings.result_dir = str(_JOB.results[-1])
                if settings.auto_import and not _JOB.cancelled:
                    if bpy.context.scene != _JOB_SCENE:
                        settings.message = 'Finished. Switch back to the original scene to import results.'
                    else:
                        errors = []
                        for dataset in _JOB.results:
                            try:
                                _, warning = import_bridge.import_dataset(dataset, settings, probe_path())
                                if warning:
                                    errors.append(warning)
                            except Exception as exc:
                                errors.append(str(exc))
                        settings.message = '; '.join(dict.fromkeys(errors)) if errors else f'Imported {len(_JOB.results)} clip collection(s)'
        redraw()
        return 0.3 if running else None
    except Exception as exc:
        _JOB.status = f'Error: {exc}'
        _JOB.close()
        redraw()
        return None


class GLOMAP_Preferences(AddonPreferences):
    bl_idname = __package__
    ffmpeg: StringProperty(name='FFmpeg executable', default='ffmpeg', subtype='FILE_PATH')
    colmap: StringProperty(name='COLMAP executable', default='colmap', subtype='FILE_PATH')
    ffprobe: StringProperty(name='FFprobe (optional, for playback FPS)', default='ffprobe', subtype='FILE_PATH')

    def draw(self, context):
        layout = self.layout
        layout.label(text='Install or check tools in the Video to 3D sidebar.')
        layout.prop(self, 'ffmpeg')
        layout.prop(self, 'colmap')
        layout.prop(self, 'ffprobe')
        layout.operator('glomap.setup_importer', icon='IMPORT')
        layout.label(text='Bundled importer: ' + ('enabled' if import_bridge.enabled() else 'not enabled'))
        layout.label(text='No optional pip packages are needed for this COLMAP import path.')


class GLOMAP_Video(PropertyGroup):
    filepath: StringProperty(subtype='FILE_PATH')


def globlender_dir(settings):
    if settings.get('globlender_path'):
        return settings['globlender_path']
    if settings.project_dir:
        return str(jobs.workspace_path(abs_path(settings.project_dir)))
    return str(Path.home() / 'Videos' / 'globlender')


def folder_field(label, key, subfolder):
    return StringProperty(
        name=label, subtype='DIR_PATH',
        description='Clear this field to use the folder under Globlender',
        get=lambda settings: settings.get(key) or str(Path(globlender_dir(settings)) / subfolder),
        set=lambda settings, value: settings.__setitem__(key, value),
    )


class GLOMAP_Settings(PropertyGroup):
    folders_open: BoolProperty(name='Globlender File Paths', default=True)
    setup_open: BoolProperty(name='Setup', default=False)
    setup_details: BoolProperty(name='Setup Details', default=False)
    terminal_open: BoolProperty(name='Live Terminal Output', default=False)
    globlender: StringProperty(
        name='Globlender', subtype='DIR_PATH',
        get=globlender_dir,
        set=lambda settings, value: settings.__setitem__('globlender_path', value),
    )
    input_folder: folder_field('Input Folder', 'input_override', 'input_videos')
    output_folder: folder_field('Output Folder', 'output_override', 'finished_tracks')
    import_options: BoolProperty(name='Import Options', default=False)
    project_dir: StringProperty(
        name='Destination', subtype='DIR_PATH',
        description='Parent folder for the glomap project directory',
    )
    videos: CollectionProperty(type=GLOMAP_Video)
    active_video: IntProperty(default=0)
    result_dir: StringProperty(name='Finished video folder', subtype='DIR_PATH')
    auto_import: BoolProperty(name='Auto-import completed clips', default=True)
    gpu: EnumProperty(name='Feature processing', items=[('auto', 'Auto GPU / CPU', ''), ('cpu', 'CPU only', ''), ('gpu', 'Try GPU, retry CPU on failure', '')], default='auto')
    mapper: EnumProperty(name='Mapping', items=[('incremental', 'Incremental', 'Default robust mapping'), ('auto', 'Global, then incremental', 'Try integrated GLOMAP when available'), ('global', 'Global required, incremental recovery', 'Require global commands to exist')], default='incremental')
    max_size: IntProperty(name='Maximum image edge', default=2000, min=64, max=32768)
    threads: IntProperty(name='CPU threads', default=4, min=1, max=256)
    features: IntProperty(name='Features per frame', default=8192, min=128, max=65536)
    overlap: IntProperty(name='Matching overlap', default=10, min=2, max=1000)
    camera_model: EnumProperty(name='Camera model', items=[(s, s, '') for s in ('SIMPLE_RADIAL', 'OPENCV', 'PINHOLE', 'OPENCV_FISHEYE')], default='SIMPLE_RADIAL')
    single_camera: BoolProperty(name='One fixed lens per clip', default=True)
    min_points: IntProperty(name='Minimum 3D points', default=100, min=1)
    max_error: FloatProperty(name='Maximum reprojection error (px)', default=2.0, min=0.01)
    advanced: BoolProperty(name='Advanced settings', default=False)
    animate_camera: BoolProperty(name='Import camera animation', default=True)
    match_fps: BoolProperty(name='Match scene FPS to footage', default=True)
    background_images: BoolProperty(name='Camera background images', default=True)
    point_display: EnumProperty(name='Point display', items=[('MESH', 'Mesh + Geometry Nodes', 'Visible and renderable points; no OpenGL draw handler'), ('OPENGL', 'OpenGL viewport points', 'Use the importer GPU viewport overlay; use an OpenGL backend')], default='MESH')
    point_radius: FloatProperty(name='Point radius', default=0.02, min=0.00001, max=10)
    message: StringProperty(options={'SKIP_SAVE'})


class GLOMAP_UL_videos(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=Path(item.filepath).name, icon='FILE_MOVIE')
        if not _JOB.running:
            op = row.operator('glomap.remove_video', text='', icon='X', emboss=False)
            op.index = index


class GLOMAP_OT_output(Operator):
    bl_idname = 'glomap.choose_output'
    bl_label = 'Choose Destination'
    bl_description = 'Choose or create a folder to contain the glomap project directory'
    directory: StringProperty(subtype='DIR_PATH')
    filter_folder: BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        self.directory = context.scene.glomap.project_dir
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            destination = jobs.project_path(abs_path(self.directory))
            jobs.workspace_path(str(destination)).mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.scene.glomap.project_dir = str(destination)
        return {'FINISHED'}


class GLOMAP_OT_add(Operator, ImportHelper):
    bl_idname = 'glomap.add_videos'
    bl_label = 'Add Video Clips'
    bl_description = 'Copy videos into Input Folder and add them to the clip list'
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype='DIR_PATH')
    filter_glob: StringProperty(default='*.mp4;*.MP4;*.mov;*.MOV;*.mkv;*.MKV;*.avi;*.webm;*.m4v;*.mts;*.m2ts;*.mpg;*.mpeg', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return not (_JOB.running or _INPUT.running)

    def invoke(self, context, event):
        try:
            folder = jobs.project_path(abs_path(context.scene.glomap.input_folder))
            folder.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.filepath = self.directory = str(folder) + os.sep
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        global _INPUT_SCENE
        settings = context.scene.glomap
        try:
            folder = jobs.project_path(abs_path(settings.input_folder))
            paths = [Path(self.directory) / item.name for item in self.files] or [Path(self.filepath)]
            sources = jobs.validate_videos(paths)
            folder.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        def copy():
            for source in sources:
                _INPUT.check_cancelled()
                _INPUT.message(f'Adding {source.name}')
                _INPUT.results.append(jobs.copy_input_clip(source, folder, _INPUT.check_cancelled))
            _INPUT.message(f'Added {len(sources)} clip(s) to Input Folder')
            return 0

        _INPUT_SCENE = context.scene
        _INPUT.launch(copy, None)
        if not bpy.app.timers.is_registered(tick_input):
            bpy.app.timers.register(tick_input, first_interval=0.15)
        return {'FINISHED'}

class GLOMAP_OT_remove(Operator):
    bl_idname = 'glomap.remove_video'
    bl_label = 'Remove Selected Clip'
    bl_description = 'Remove this clip from the selection without deleting its source file'
    index: IntProperty()

    def execute(self, context):
        if not _JOB.running and 0 <= self.index < len(context.scene.glomap.videos):
            context.scene.glomap.videos.remove(self.index)
        return {'FINISHED'}


class GLOMAP_OT_setup(Operator):
    bl_idname = 'glomap.setup_importer'
    bl_label = 'Set Up Included Photogrammetry Importer'
    bl_description = 'Enable the bundled importer from disk without installing optional Python packages'

    def execute(self, context):
        global _IMPORTER_ERROR
        _IMPORTER_ERROR = ''
        try:
            message = import_bridge.setup()
            context.scene.glomap.message = message
            self.report({'INFO'}, message)
            return {'FINISHED'}
        except Exception as exc:
            detail = traceback.format_exc()
            hint = ''
            cause = exc
            while cause is not None:
                hint = windows_error_hint(getattr(cause, 'winerror', None))
                if hint:
                    break
                cause = cause.__cause__
            _IMPORTER_ERROR = f'{exc} {hint}'.strip()
            try:
                root = tool_root()
                root.mkdir(parents=True, exist_ok=True)
                (root/'importer-setup.log').write_text(
                    f'{_IMPORTER_ERROR}\nBlender {bpy.app.version_string}\n{detail}', encoding='utf-8')
                _IMPORTER_ERROR += ' See importer-setup.log.'
            except OSError as log_error:
                _IMPORTER_ERROR += f' Could not write importer log: {log_error}'
            context.scene.glomap.setup_details = True
            self.report({'ERROR'}, _IMPORTER_ERROR)
            return {'CANCELLED'}


class GLOMAP_OT_check(Operator):
    bl_idname = 'glomap.check_tools'
    bl_label = 'Check Setup'
    bl_description = 'Run COLMAP, FFmpeg and FFprobe individually and show their status'

    @classmethod
    def poll(cls, context):
        return not (_JOB.running or _SETUP.running)

    def execute(self, context):
        global _INSTALLING
        try:
            root = tool_root()
            root.mkdir(parents=True, exist_ok=True)
            values = {name: abs_path(getattr(preferences(), name)) for name in _TOOL_NAMES}
            env = tool_setup.environment(root)
            _TOOL_RESULTS.clear()
            _INSTALLING = False

            def check():
                failures = []
                for name, value in values.items():
                    _SETUP.check_cancelled()
                    try:
                        executable = tool_setup.resolve_tool(name, value, root)
                        runner = jobs.Pipeline(_SETUP, root, {}, {name: executable}, env, root)
                        runner.run_command(name, tool_setup.CHECKS[name], root/f'check-{name}.log')
                    except (OSError, RuntimeError, ValueError) as exc:
                        _TOOL_RESULTS[name] = (value, str(exc))
                        failures.append(name.upper())
                    else:
                        _TOOL_RESULTS[name] = (value, '')
                _SETUP.message('Check: ' + (', '.join(failures) + ' unavailable' if failures else 'All three tools ready'))
                return 0

            _SETUP.launch(check, root/'setup-status.log')
            if not bpy.app.timers.is_registered(tick_setup):
                bpy.app.timers.register(tick_setup, first_interval=0.2)
            self.report({'INFO'}, 'Checking tool executables. See Setup for results.')
            return {'FINISHED'}
        except (OSError, ValueError) as exc:
            _SETUP.status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class GLOMAP_OT_install_tools(Operator):
    bl_idname = 'glomap.install_tools'
    bl_label = 'Install Tools'
    bl_description = 'Install and verify CPU COLMAP, FFmpeg and FFprobe in Blender’s user data folder'

    @classmethod
    def poll(cls, context):
        return not (_JOB.running or _SETUP.running)

    def execute(self, context):
        global _INSTALLING
        if not tool_setup.bundled_tools() and not getattr(bpy.app, 'online_access', True):
            self.report({'ERROR'}, 'Enable Allow Online Access in Blender Preferences to download tools')
            return {'CANCELLED'}
        try:
            root = tool_root()
            root.mkdir(parents=True, exist_ok=True)
            _TOOL_RESULTS.clear()
            _INSTALLING = True
            context.scene.glomap.setup_details = True
            _SETUP.launch(lambda: tool_setup.install(_SETUP, root, root), root/'setup-status.log')
            if not bpy.app.timers.is_registered(tick_setup):
                bpy.app.timers.register(tick_setup, first_interval=0.2)
            return {'FINISHED'}
        except OSError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class GLOMAP_OT_cancel_setup(Operator):
    bl_idname = 'glomap.cancel_setup'
    bl_label = 'Cancel Tool Setup'
    bl_description = 'Stop the current tool check or installation'

    def execute(self, context):
        _SETUP.cancel()
        return {'FINISHED'}


class GLOMAP_OT_start(Operator):
    bl_idname = 'glomap.start'
    bl_label = 'Run GLOMAP'
    bl_description = 'Reconstruct selected clips or all videos in the input folder'

    @classmethod
    def poll(cls, context):
        return not (_JOB.running or _SETUP.running or _INPUT.running)

    def execute(self, context):
        global _JOB_SCENE, _POST_IMPORT
        settings = context.scene.glomap
        try:
            sources = jobs.validate_videos([item.filepath for item in settings.videos])
            if settings.auto_import and not import_bridge.enabled():
                raise ValueError('Set up the included importer before enabling auto-import')
            root = jobs.project_path(abs_path(settings.globlender))
            tools = {name: find_tool(name) for name in ('ffmpeg', 'colmap')}
            if probe := probe_path():
                tools['ffprobe'] = probe
            input_folder = Path(abs_path(settings.input_folder)).resolve()
            output_folder = Path(abs_path(settings.output_folder)).resolve()
            if input_folder == output_folder:
                raise ValueError('Input Folder and Output Folder must be different')
            settings_dict = {
                'GPU_MODE': settings.gpu, 'MAPPER': settings.mapper,
                'MAX_IMAGE_SIZE': settings.max_size, 'THREADS': settings.threads,
                'MAX_FEATURES': settings.features, 'OVERLAP': settings.overlap,
                'CAMERA_MODEL': settings.camera_model, 'SINGLE_CAMERA': int(settings.single_camera),
                'MIN_POINTS': settings.min_points, 'MAX_REPROJECTION_ERROR': settings.max_error,
                'MIN_REGISTERED_PERCENT': '100',
            }
            _JOB.start(root, sources, settings_dict, tools, tool_setup.environment(tool_root()), output_folder)
            _JOB_SCENE = context.scene
            _POST_IMPORT = False
            settings.message = f'Processing {len(sources)} selected clip(s).'
            if not bpy.app.timers.is_registered(tick):
                bpy.app.timers.register(tick, first_interval=0.3)
            return {'FINISHED'}
        except Exception as exc:
            settings.message = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class GLOMAP_OT_cancel(Operator):
    bl_idname = 'glomap.cancel'
    bl_label = 'Cancel Reconstruction'
    bl_description = 'Stop reconstruction and keep completed clips'

    @classmethod
    def poll(cls, context):
        return _JOB.running and not _JOB.cancelled

    def execute(self, context):
        _JOB.cancel()
        self.report({'INFO'}, 'Stopping reconstruction')
        redraw()
        return {'FINISHED'}


class GLOMAP_OT_pause(Operator):
    bl_idname = 'glomap.pause'
    bl_label = 'Pause / Resume Reconstruction'
    bl_description = 'Pause or resume the active tool or hold a queued clip for later'
    index: IntProperty(default=-1, min=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not jobs.PAUSE_SUPPORTED:
            cls.poll_message_set('Live pause is available on Linux and macOS')
            return False
        return _JOB.running and not _JOB.cancelled

    def execute(self, context):
        if self.index >= 0:
            if self.index >= len(_JOB.clips) or _JOB.clips[self.index].status in jobs.TERMINAL_CLIP_STATUSES:
                return {'CANCELLED'}
            _JOB.toggle_pause(self.index)
        else:
            _JOB.toggle_pause()
        redraw()
        return {'FINISHED'}


class GLOMAP_OT_import(Operator):
    bl_idname = 'glomap.import_result'
    bl_label = 'Import Finished Clip'
    bl_description = 'Import a finished COLMAP reconstruction into the current scene'
    directory: StringProperty(subtype='DIR_PATH')

    @classmethod
    def poll(cls, context):
        return not _JOB.running

    def invoke(self, context, event):
        if self.directory or context.scene.glomap.result_dir:
            return self.execute(context)
        self.directory = abs_path(context.scene.glomap.output_folder) + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            settings = context.scene.glomap
            path = abs_path(self.directory or settings.result_dir)
            if not path:
                raise ValueError('Choose a finished video folder first')
            collection, warning = import_bridge.import_dataset(path, settings, probe_path())
            settings.result_dir = str(Path(path).resolve())
            settings.message = warning or f'Imported into {collection.name}'
            self.report({'WARNING'} if warning else {'INFO'}, settings.message)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            context.scene.glomap.message = str(exc)
            return {'CANCELLED'}


class GLOMAP_OT_clear_setup_output(Operator):
    bl_idname = 'glomap.clear_setup_output'
    bl_label = 'Clear Setup Output'
    bl_description = 'Clear panel output without deleting log files or changing installed tools'

    def execute(self, context):
        global _IMPORTER_ERROR
        with _SETUP.console_lock:
            _SETUP.console.clear()
        _IMPORTER_ERROR = ''
        if not _SETUP.running:
            _SETUP.status = 'Ready'
            _SETUP.install_step = None
        context.scene.glomap.setup_details = False
        return {'FINISHED'}


class GLOMAP_OT_logs(Operator):
    bl_idname = 'glomap.open_logs'
    bl_label = 'Open Logs Folder'
    bl_description = 'Open the folder containing reconstruction or setup logs'
    setup: BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.setup:
            path = _SETUP.log_path.parent if _SETUP.log_path else tool_root()
            if not path.is_dir():
                self.report({'ERROR'}, 'No setup logs yet')
                return {'CANCELLED'}
            return bpy.ops.wm.path_open(filepath=str(path))
        try:
            root = jobs.project_path(abs_path(context.scene.glomap.globlender))
        except (OSError, ValueError, RuntimeError):
            self.report({'ERROR'}, 'Choose a destination first')
            return {'CANCELLED'}
        path = (_JOB.command_log or _JOB.log_path) if _JOB.root == root else None
        path = path.parent if path and path.parent.is_dir() else root / jobs.WORK_DIR
        if not path.is_dir():
            path = root / jobs.LEGACY_WORK_DIR
        if not path.is_dir():
            self.report({'ERROR'}, 'No logs folder exists yet')
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=str(path))
        return {'FINISHED'}


class GLOMAP_OT_copy_terminal(Operator):
    bl_idname = 'glomap.copy_terminal'
    bl_label = 'Copy Recent Output'
    bl_description = 'Copy the last 100 lines of terminal output to the clipboard'
    setup: BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.setup:
            lines, _, _, _ = _SETUP.console_snapshot()
            details = [_IMPORTER_ERROR, _SETUP.status, *lines]
        else:
            details, _, _, _ = _JOB.console_snapshot()
        context.window_manager.clipboard = '\n'.join(line for line in details if line)
        self.report({'INFO'}, 'Recent terminal output copied')
        return {'FINISHED'}


class GLOMAP_PT_sidebar(Panel):
    bl_label = 'Video to 3D'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Video to 3D'

    def draw(self, context):
        settings = context.scene.glomap
        layout = self.layout
        setup = layout.box()
        setup.prop(settings, 'setup_open', icon='TRIA_DOWN' if settings.setup_open else 'TRIA_RIGHT', emboss=False)
        if settings.setup_open:
            check = setup.row()
            check.scale_y = 1.5
            check.operator('glomap.check_tools', icon='CHECKMARK')
            setup.operator('glomap.install_tools', icon='IMPORT')
            if not import_bridge.enabled():
                setup.operator('glomap.setup_importer', text='Set Up Importer', icon='IMPORT')
            if _SETUP.running:
                setup.operator('glomap.cancel_setup', icon='CANCEL')
            prefs = preferences()
            for prop in _TOOL_NAMES:
                row = setup.row(align=True)
                value, error = _TOOL_RESULTS.get(prop, (None, ''))
                if value != abs_path(getattr(prefs, prop)):
                    row.label(text='', icon='QUESTION')
                elif error:
                    row.label(text='', icon='ERROR')
                else:
                    row.label(text='', icon_value=_ICONS['ready'].icon_id)
                row.prop(prefs, prop, text=prop.upper())
            step = _SETUP.install_step
            if _INSTALLING and step:
                number, done, total = step
                setup.label(text=f'Step {number} of 4', icon='TIME' if _SETUP.running else 'INFO')
                if number == 1:
                    label = f'{done / (1024 ** 2):.1f} MiB'
                    if total:
                        label += f' / {total / (1024 ** 2):.1f} MiB'
                elif number == 3 and total and done <= total:
                    label = f'{done} / {total} packages linked'
                elif number == 4:
                    label = f'{done} / {total} tools verified'
                else:
                    label = ''
                if label:
                    if total and hasattr(setup, 'progress'):
                        setup.progress(factor=min(1.0, done / total), type='BAR', text=label)
                    else:
                        setup.label(text=label)
            scale = context.preferences.system.ui_scale or 1
            width = max(22, int(context.region.width / scale / 7) - 6)
            if _SETUP.status != 'Ready':
                for line in textwrap.wrap(_SETUP.status, width=width):
                    setup.label(text=line)
            setup.prop(settings, 'setup_details',
                       icon='TRIA_DOWN' if settings.setup_details else 'TRIA_RIGHT', emboss=False)
            if settings.setup_details:
                if _IMPORTER_ERROR:
                    for line in textwrap.wrap(_IMPORTER_ERROR, width=width):
                        setup.label(text=line)
                for name in _TOOL_NAMES:
                    value, error = _TOOL_RESULTS.get(name, (None, ''))
                    if error and value == abs_path(getattr(prefs, name)):
                        for line in textwrap.wrap(error, width=width):
                            setup.label(text=line)
                lines, started, last_output, _ = _SETUP.console_snapshot()
                if _SETUP.running and started is not None:
                    seconds = int(monotonic() - (last_output or started))
                    label = 'Last tool output' if last_output is not None else 'Waiting for tool output'
                    setup.label(text=f'{label} · {seconds}s', icon='TIME')
                for line in lines[-5:]:
                    for part in textwrap.wrap(line, width=width, max_lines=2, placeholder='…'):
                        setup.label(text=part, translate=False)
                if lines or _IMPORTER_ERROR:
                    setup.operator('glomap.copy_terminal', text='Copy Setup Details', icon='COPYDOWN').setup = True
                setup.operator('glomap.open_logs', text='Open Setup Logs', icon='TEXT').setup = True
            setup.operator('glomap.clear_setup_output', icon='X')

        folders = layout.box()
        folders.prop(settings, 'folders_open',
                     icon='TRIA_DOWN' if settings.folders_open else 'TRIA_RIGHT', emboss=False)
        if settings.folders_open:
            fields = folders.column()
            fields.enabled = not (_JOB.running or _INPUT.running)
            for prop in ('globlender', 'input_folder', 'output_folder'):
                fields.label(text=settings.bl_rna.properties[prop].name)
                fields.prop(settings, prop, text='')
            fields.label(text='Video List to process')
            clips = fields.row()
            clips.template_list('GLOMAP_UL_videos', '', settings, 'videos', settings, 'active_video', rows=2)
            add_column = clips.column(align=True)
            add_column.operator_context = 'INVOKE_DEFAULT'
            add_column.operator('glomap.add_videos', text='', icon='ADD')
            if _INPUT.running:
                folders.label(text=_INPUT.status, icon='TIME')
            elif not settings.videos:
                folders.label(text='Use + to select clips')

        settings_box = layout.box()
        settings_box.enabled = not _JOB.running
        settings_box.label(text='COLMAP Settings')
        for prop in ('gpu', 'mapper', 'max_size'):
            settings_box.prop(settings, prop)
        settings_box.prop(settings, 'advanced', icon='TRIA_DOWN' if settings.advanced else 'TRIA_RIGHT', emboss=False)
        if settings.advanced:
            for prop in ('threads', 'features', 'overlap', 'camera_model', 'single_camera', 'min_points', 'max_error'):
                settings_box.prop(settings, prop)

        actions = layout.column(align=True)
        actions.prop(settings, 'auto_import', text='Automatically Import Output')
        run = actions.row()
        run.scale_y = 1.5
        run.operator('glomap.start', icon='PLAY')
        actions.operator_context = 'INVOKE_DEFAULT'
        actions.operator('glomap.import_result', text='Import Output to Project', icon='IMPORT')
        actions.prop(settings, 'import_options', icon='TRIA_DOWN' if settings.import_options else 'TRIA_RIGHT', emboss=False)
        if settings.import_options:
            for prop in ('animate_camera', 'match_fps', 'background_images', 'point_display', 'point_radius'):
                actions.prop(settings, prop)
        if _JOB.running:
            controls = layout.row(align=True)
            controls.operator('glomap.pause', text='Resume' if _JOB.paused else 'Pause',
                              icon='PLAY' if _JOB.paused else 'PAUSE')
            controls.operator('glomap.cancel', text='Stopping…' if _JOB.cancelled else 'Cancel',
                            icon='CANCEL')
        layout.separator()
        status = _JOB.status
        if _JOB.running:
            if _JOB.cancelled:
                status = 'Stopping reconstruction…'
            elif _JOB.paused:
                status = 'Batch paused'
            elif _JOB.waiting_for_clips:
                status = 'Waiting for paused clips'
        layout.label(text=status, icon='TIME' if _JOB.running else 'INFO')
        if _JOB_SCENE == context.scene and _JOB.clips:
            lines, started, last_output, counter = _JOB.console_snapshot()
            progress = layout.box()
            progress.label(text='Clip tasks')
            for index, clip in enumerate(_JOB.clips):
                row = progress.row(align=True)
                row.label(text=clip.name, icon='FILE_MOVIE')
                can_pause = _JOB.running and clip.status not in jobs.TERMINAL_CLIP_STATUSES
                individually_paused = index in _JOB.paused_clips
                if can_pause:
                    row.operator('glomap.pause', text='',
                                 icon='PLAY' if individually_paused else 'PAUSE').index = index
                progress.label(text=f'Paused · {clip.status}' if can_pause and _JOB.is_paused(index)
                               else clip.status)
                if (_JOB.running and counter and counter[:2] == (index, clip.stage)
                        and clip.status not in ('Finished', 'Already finished', 'Failed')):
                    current, total, unit, in_progress = counter[2:]
                    if total:
                        label = f'{current}/{total} {unit}'
                        if in_progress:
                            label = f'Working on {label}'
                        if hasattr(progress, 'progress'):
                            progress.progress(factor=(current - int(in_progress)) / total,
                                              type='BAR', text=label)
                        else:
                            progress.label(text=label)
                    else:
                        progress.label(text=f'{current} {unit} processed')

            terminal = layout.box()
            terminal.prop(settings, 'terminal_open',
                          icon='TRIA_DOWN' if settings.terminal_open else 'TRIA_RIGHT', emboss=False)
            if settings.terminal_open:
                if _JOB.running and started is not None:
                    now = monotonic()
                    terminal.label(text=f'Command started {int(now - started)}s ago', icon='TIME')
                    if last_output is None:
                        terminal.label(text='Waiting for tool output')
                    else:
                        terminal.label(text=f'Last output {int(now - last_output)}s ago')
                if lines:
                    scale = context.preferences.system.ui_scale or 1
                    width = max(22, int(context.region.width / scale / 7) - 6)
                    for line in lines[-8:]:
                        wrapped = textwrap.wrap(line, width=width, max_lines=2, placeholder='…')
                        for part in wrapped:
                            terminal.label(text=part, translate=False)
                    terminal.operator('glomap.copy_terminal', icon='COPYDOWN')
                else:
                    terminal.label(text='Waiting for terminal output' if _JOB.running else 'No terminal output captured')
        if settings.message:
            # Wrap messages to fit the sidebar.
            for line in textwrap.wrap(settings.message, width=34)[:6]:
                layout.label(text=line)
        layout.operator('glomap.open_logs', icon='TEXT')


def shutdown():
    _JOB.close()
    _SETUP.close()
    _INPUT.close()


@persistent
def before_load(_):
    global _JOB_SCENE, _POST_IMPORT, _INPUT_SCENE
    shutdown()
    _JOB_SCENE = None
    _INPUT_SCENE = None
    _POST_IMPORT = True


_CLASSES = (
    GLOMAP_Preferences, GLOMAP_Video, GLOMAP_Settings, GLOMAP_UL_videos,
    GLOMAP_OT_output, GLOMAP_OT_add, GLOMAP_OT_remove,
    GLOMAP_OT_setup, GLOMAP_OT_check, GLOMAP_OT_install_tools, GLOMAP_OT_cancel_setup, GLOMAP_OT_clear_setup_output, GLOMAP_OT_start, GLOMAP_OT_cancel,
    GLOMAP_OT_pause, GLOMAP_OT_import, GLOMAP_OT_logs, GLOMAP_OT_copy_terminal, GLOMAP_PT_sidebar,
)


def register():
    global _ICONS
    _ICONS = bpy.utils.previews.new()
    _ICONS.load('ready', str(Path(__file__).with_name('ready.png')), 'IMAGE')
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.glomap = PointerProperty(type=GLOMAP_Settings)
    bpy.app.handlers.load_pre.append(before_load)
    atexit.register(shutdown)


def unregister():
    global _ICONS
    shutdown()
    bpy.utils.previews.remove(_ICONS)
    _ICONS = None
    for timer in (tick, tick_setup, tick_input):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    if before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(before_load)
    atexit.unregister(shutdown)
    del bpy.types.Scene.glomap
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
