"""Run in isolated Blender: --background --factory-startup --python this.py -- addon.zip."""
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy


archive = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
bpy.ops.preferences.addon_install(filepath=str(archive))
addon_utils.enable('blender_glomap', default_set=False)
import blender_glomap as addon


with tempfile.TemporaryDirectory(prefix='glomap-smoke-') as temp:
    root = Path(temp)
    source = root/'new clip.mp4'
    source.write_bytes(b'Copy-task fixture; no decoding is performed during Add Video Clips.')
    settings = bpy.context.scene.glomap
    settings.input_folder = str(root/'input')
    assert bpy.ops.glomap.add_videos(filepath=str(source)) == {'FINISHED'}
    addon._INPUT.thread.join()
    addon.tick_input()
    assert addon._INPUT.returncode == 0
    assert len(settings.videos) == 1
    copied = Path(settings.videos[0].filepath)
    assert copied.read_bytes() == source.read_bytes()
    print('PASS: adding a clip in a new Blender file')

    assert bpy.ops.glomap.setup_importer() == {'FINISHED'}
    dataset = root/'dataset'
    model = dataset/'sparse'/'0'
    model.mkdir(parents=True)
    images = dataset/'images'
    images.mkdir()
    (model/'cameras.txt').write_text('1 PINHOLE 32 48 40 40 16 24\n')
    (model/'images.txt').write_text(
        '1 1 0 0 0 0 0 0 1 frame_000001.png\n16 24 1\n'
        '2 1 0 0 0 0.1 0 0 1 frame_000002.png\n17 24 1\n')
    (model/'points3D.txt').write_text('1 0 0 3 255 128 64 0.1 1 0 2 0\n')
    for index in (1, 2):
        image = bpy.data.images.new(f'Fixture {index}', width=32, height=48)
        image.generated_color = (0.8, 0.2, 0.1, 1)
        image.filepath_raw = str(images/f'frame_{index:06d}.png')
        image.file_format = 'PNG'
        image.save()
        bpy.data.images.remove(image)
    settings.background_images = True
    settings.animate_camera = True
    settings.match_fps = False
    collection, _ = addon.import_bridge.import_dataset(dataset, settings)
    camera = bpy.context.scene.camera
    assert camera in list(collection.all_objects)
    assert camera.animation_data and camera.animation_data.action
    backgrounds = [background for obj in collection.all_objects if obj.type == 'CAMERA'
                   for background in obj.data.background_images]
    assert len(backgrounds) >= 3, 'Expected animated and per-frame camera backgrounds'
    for obj in collection.all_objects:
        if obj.type == 'CAMERA':
            assert obj.data.show_background_images
    for background in backgrounds:
        assert background.alpha == 1.0 and background.show_background_image
        assert background.display_depth == 'BACK'
    animated = camera.data.background_images[0]
    assert animated.source == 'MOVIE_CLIP' and animated.clip is not None
    assert not animated.use_camera_clip
    assert tuple(animated.clip.size) == (32, 48), 'Animated background could not load its image'
    print('PASS: imported camera animation and readable full-opacity backgrounds')

    animated.alpha = 0.1
    animated.show_background_image = False
    camera.data.show_background_images = False
    object_count = len(bpy.data.objects)
    reused, _ = addon.import_bridge.import_dataset(dataset, settings)
    assert reused == collection and len(bpy.data.objects) == object_count
    assert animated.alpha == 1.0 and animated.show_background_image
    assert camera.data.show_background_images
    print('PASS: re-import restores background display without duplicating the reconstruction')
