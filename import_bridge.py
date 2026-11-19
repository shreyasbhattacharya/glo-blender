"""Manage the separately registered, bundled Photogrammetry Importer."""
from array import array
import importlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import traceback
from fractions import Fraction
from zipfile import ZipFile

import addon_utils
import bpy

MODULE = 'photogrammetry_importer'


def enabled():
    return addon_utils.check(MODULE)[1]


def setup():
    if enabled():
        return 'Photogrammetry Importer is already enabled'
    installed = next((m for m in addon_utils.modules(refresh=True) if m.__name__ == MODULE), None)
    if installed is None:
        version = 'blender5' if bpy.app.version >= (4, 4, 0) else 'blender4'
        archive = Path(__file__).parent / 'bundled' / f'photogrammetry_importer_{version}.zip'
        destination = bpy.utils.user_resource('SCRIPTS', path='addons')
        if not destination:
            raise RuntimeError('Blender did not provide a user add-ons folder')
        addons = Path(destination)
        target = addons / MODULE
        if target.exists():
            raise RuntimeError(f'Blender could not load the existing importer at {target}')
        try:
            addons.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=addons, prefix='.importer-') as staging:
                with ZipFile(archive) as bundle:
                    bundle.extractall(staging)
                (Path(staging) / MODULE).rename(target)
        except Exception as exc:
            raise RuntimeError(f'Could not install {archive.name} into {addons}: {exc}') from exc
        importlib.invalidate_caches()
        bpy.utils.refresh_script_paths()
        addon_utils.modules_refresh()
    errors = []
    addon_utils.enable(MODULE, default_set=True, persistent=True, handle_error=errors.append)
    if errors:
        error = errors[-1]
        raise RuntimeError(f'Could not enable Photogrammetry Importer: {import_error_details(error)}') from error
    if not enabled():
        raise RuntimeError('Blender did not enable Photogrammetry Importer')
    return 'Bundled Photogrammetry Importer installed and enabled'


def source_rate(video, probe):
    if not video or not Path(video).is_file():
        return None
    try:
        result = subprocess.run([
            probe, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=avg_frame_rate,r_frame_rate', '-of', 'json', str(video),
        ], capture_output=True, text=True, timeout=10, check=True)
        stream = json.loads(result.stdout)['streams'][0]
        avg = Fraction(stream['avg_frame_rate'])
        nominal = Fraction(stream['r_frame_rate'])
        if avg <= 0 or nominal <= 0:
            return None
        # A mismatch is a warning, not a reliable VFR detector.
        return avg, avg != nominal
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return None


def add_point_geometry(path, collection, radius):
    coordinates = []
    colors = array('f')
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip() or line.startswith('#'):
                continue
            fields = line.split()
            coordinates.append(tuple(float(value) for value in fields[1:4]))
            colors.extend(int(value) / 255 for value in fields[4:7])
            colors.append(1)
    if not coordinates:
        return

    mesh = bpy.data.meshes.new('GLOMAP Points')
    mesh.from_pydata(coordinates, [], [])
    mesh.update()
    attribute = mesh.color_attributes.new(name='point_color', type='FLOAT_COLOR', domain='POINT')
    attribute.data.foreach_set('color', colors)
    obj = bpy.data.objects.new('GLOMAP Points', mesh)
    collection.objects.link(obj)

    material = bpy.data.materials.new('GLOMAP Point Color')
    material.use_nodes = True
    tree = material.node_tree
    color = tree.nodes.new('ShaderNodeAttribute')
    color.attribute_name = attribute.name
    emission = tree.nodes.new('ShaderNodeEmission')
    output = next(node for node in tree.nodes if node.type == 'OUTPUT_MATERIAL')
    tree.links.new(color.outputs['Color'], emission.inputs['Color'])
    tree.links.new(emission.outputs[0], output.inputs['Surface'])

    group = bpy.data.node_groups.new('GLOMAP Point Display', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    source = group.nodes.new('NodeGroupInput')
    points = group.nodes.new('GeometryNodeMeshToPoints')
    surface = group.nodes.new('GeometryNodeSetMaterial')
    target = group.nodes.new('NodeGroupOutput')
    points.inputs['Radius'].default_value = radius
    surface.inputs['Material'].default_value = material
    group.links.new(source.outputs['Geometry'], points.inputs['Mesh'])
    group.links.new(points.outputs['Points'], surface.inputs['Geometry'])
    group.links.new(surface.outputs['Geometry'], target.inputs['Geometry'])
    obj.modifiers.new('Point Display', 'NODES').node_group = group


def import_error_details(exc):
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return next((line for line in reversed(lines) if not line.startswith('Location:')), type(exc).__name__)


def import_dataset(dataset, settings, probe='ffprobe'):
    if not enabled():
        raise RuntimeError('Run Set Up Included Importer first')
    if bpy.context.mode != 'OBJECT':
        raise RuntimeError('Switch Blender to Object Mode before importing')
    dataset = Path(dataset).expanduser().resolve(strict=True)
    model = dataset / 'sparse' / '0'
    for name in ('cameras.txt', 'images.txt', 'points3D.txt'):
        if not (model / name).is_file():
            raise ValueError('Select the finished video folder containing images/ and sparse/0/')
    images = dataset / 'images'
    if not images.is_dir():
        raise ValueError('The dataset image directory is missing')
    for collection in bpy.data.collections:
        if collection.get('glomap_dataset') == str(dataset) and collection.get('glomap_complete'):
            return collection, 'Already imported; existing collection kept'
    video = ''
    metadata = dataset / 'source-and-settings.txt'
    if metadata.is_file():
        for line in metadata.read_text(encoding='utf-8').splitlines():
            if line.startswith('source='):
                video = line[len('source='):]
                break
    previous = set(bpy.data.collections)
    previous_objects = set(bpy.data.objects)
    label = dataset.name
    parent = bpy.data.collections.new(f'GLOMAP | {label}')
    bpy.context.scene.collection.children.link(parent)
    parent['glomap_dataset'] = str(dataset)
    parent['glomap_video'] = video
    parent['glomap_complete'] = False
    point_mesh = settings.point_display == 'MESH'
    options = dict(
        directory=str(model) + os.sep, image_dp=str(images), image_fp_type='NAME',
        use_workspace_images=False, suppress_distortion_warnings=True,
        import_cameras=True, import_points=not point_mesh, import_mesh=False,
        add_camera_motion_as_animation=settings.animate_camera,
        animation_frame_source='ORIGINAL', number_interpolation_frames=0,
        consider_missing_cameras_during_animation=True,
        interpolation_type='LINEAR', remove_rotation_discontinuities=True,
        add_background_images=settings.background_images,
        add_animated_camera_background_images=settings.background_images,
        add_image_planes=False, add_depth_maps_as_point_cloud=False,
        reorganize_undistorted_images=False, center_points=False,
        draw_points_with_gpu=not point_mesh, add_points_as_mesh_oject=False,
        add_mesh_to_point_geometry_nodes=False, point_radius=settings.point_radius,
        point_subdivisions=0, adjust_clipping_distance=False,
    )
    try:
        result = bpy.ops.import_scene.colmap_model('EXEC_DEFAULT', **options)
        if 'FINISHED' not in result:
            raise RuntimeError('Photogrammetry Importer did not finish')
        if point_mesh:
            add_point_geometry(model / 'points3D.txt', parent, settings.point_radius)
    except Exception as exc:
        for obj in set(bpy.data.objects) - previous_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in set(bpy.data.collections) - previous:
            bpy.data.collections.remove(collection)
        try:
            details = (f'Blender: {bpy.app.version_string}\nDataset: {dataset}\n'
                       f'Model directory passed to Phot: {options["directory"]!r}\n'
                       f'Model entries: {sorted(p.name for p in model.iterdir())!r}\n'
                       f'Images directory: {images}\n')
            (dataset / 'import-error.log').write_text(details + traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass
        raise RuntimeError(f'Track saved. Blender import failed: {import_error_details(exc)}') from exc
    else:
        # The importer may link below the active collection rather than scene root.
        created = set(bpy.data.collections) - previous - {parent}
        owners = [bpy.context.scene.collection, *bpy.data.collections]
        for collection in created:
            linked_from = [owner for owner in owners if collection.name in owner.children]
            if any(owner in created for owner in linked_from):
                continue
            if collection.name not in parent.children:
                parent.children.link(collection)
            for owner in linked_from:
                if owner != parent:
                    owner.children.unlink(collection)
        # Hide the per-frame cameras in the viewport. Blender may suffix the
        # collection name (for example, "Cameras.001") on subsequent imports,
        # so identify camera collections by their contents, not their name.
        camera_collections = [
            collection for collection in created
            if settings.animate_camera and collection != parent
            and collection.objects
            and all(obj.type == 'CAMERA' and not (obj.animation_data and obj.animation_data.action)
                    for obj in collection.all_objects)
        ]

        def hide_layer_collection(layer_collection, target):
            if layer_collection.collection == target:
                layer_collection.hide_viewport = True
                return True
            return any(
                hide_layer_collection(child, target)
                for child in layer_collection.children
            )

        for cameras in camera_collections:
            cameras.hide_viewport = True
            for view_layer in bpy.context.scene.view_layers:
                hide_layer_collection(view_layer.layer_collection, cameras)
    parent['glomap_complete'] = True
    warning = ''
    rate_info = source_rate(video, probe)
    scene = bpy.context.scene
    if rate_info:
        rate, uncertain = rate_info
        parent['glomap_source_fps'] = float(rate)
        if settings.match_fps:
            other = [c for c in bpy.data.collections if c != parent and c.get('glomap_complete') and c.name in scene.collection.children]
            current = scene.render.fps / scene.render.fps_base
            if other and abs(current - float(rate)) > 0.001:
                warning = 'Different clip FPS: scene FPS kept. Use separate scenes for different playback rates.'
            else:
                scene.render.fps = round(float(rate))
                scene.render.fps_base = round(float(rate)) / float(rate)
        if uncertain:
            warning = 'Source frame rates differ; variable-rate timing is not reconstructed. All source frames were retained.'
    elif settings.match_fps:
        warning = 'Could not read source FPS. Set the scene frame rate manually.'
    if settings.animate_camera:
        cameras = [o for o in parent.all_objects if o.type == 'CAMERA' and o.animation_data and o.animation_data.action]
        if cameras:
            scene.camera = cameras[-1]
            scene.frame_start = 1
            scene.frame_set(1)
    return parent, warning
