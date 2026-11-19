# Video to 3D — Blender GLOMAP

An install-from-disk Blender add-on for the existing all-frame video reconstruction pipeline. Open the 3D View sidebar with **N**, then select **Video to 3D**. All controls are in that tab.

## Install

### Release choices and platform status

- **Regular ZIP (`blender_glomap-v0.3.13.zip`):** Linux installation and **Install Tools** have been tested on the development machine. Testing on other operating systems is pending.
- **Windows x64 tools-included ZIP (`blender_glomap-v0.3.13-windows-x64.zip`):** includes COLMAP, FFmpeg, FFprobe and their bundled dependencies. Native Windows testing is pending. Uses included executables without Micromamba installation; **Install Tools** verifies them and can run offline.
- Both variants install directly into Blender and include Phot. Install only one variant at a time. The Windows bundle does not guarantee that Windows Security will allow the executables, nor does it resolve a Phot import error by itself.

### Manual tools (recommended on other platforms while testing is pending)

On **macOS**, with [Homebrew](https://brew.sh/) installed:

```sh
brew install colmap ffmpeg
colmap help
ffmpeg -version
ffprobe -version
```

On **Windows**, with [Miniforge](https://github.com/conda-forge/miniforge) installed, run in its prompt. This is an optional manual Conda setup, not required for the bundled release:

```bat
conda create -n glomap-tools --override-channels -c conda-forge "colmap=4.2.1=cpu*" "ffmpeg>=7.1" -y
conda activate glomap-tools
colmap help
ffmpeg -version
ffprobe -version
where colmap
where ffmpeg
where ffprobe
```

For **Ubuntu/Debian**, system packages are another option (versions vary by distribution):

```sh
sudo apt update
sudo apt install colmap ffmpeg
colmap help
ffmpeg -version
ffprobe -version
```

FFprobe comes with FFmpeg in these packages. These are setup instructions, not claims of native macOS/Windows validation. In Blender, select executable paths under **Setup** and run **Check Setup**. On macOS/Linux, `command -v colmap ffmpeg ffprobe` prints the paths; on Windows use the `where` results. Windows COLMAP and its DLL dependencies must remain together. Installing through a different package manager does not bypass a security block.

### Building releases

Run `python scripts/package.py` for the regular ZIP. For the Windows edition, provide the official COLMAP 4.2.1 non-CUDA Windows ZIP and the FFmpeg Windows essentials ZIP:

```sh
python scripts/package.py --colmap /path/to/colmap.zip --ffmpeg /path/to/ffmpeg.zip
```

Outputs and SHA-256 sidecars go into `deliverables/` (excluded from Git). Dependencies and upstream notices are retained; archive hashes are recorded inside the Windows package. Source archives: [COLMAP 4.2.1](https://github.com/colmap/colmap/releases/tag/4.2.1) and [FFmpeg Windows builds](https://www.gyan.dev/ffmpeg/builds/). The FFmpeg build's license and corresponding-source information accompany that distribution. Preserve these notices when redistributing.

1. Keep `blender_glomap.zip` zipped. In Blender, open **Edit → Preferences → Add-ons → Install from Disk**, select it, and enable **Video to 3D — Blender GLOMAP**. Depending on the Blender version, Install from Disk is in the add-on preferences dropdown.
2. Open the **Video to 3D** sidebar tab. Click **Set Up Included Photogrammetry Importer** once. Its ZIP is already inside this package: a separate download is not required. The setup button installs and enables it as a normal, separate Blender add-on. An existing enabled installation is reused and never overwritten.
3. Expand **Setup** and click **Check Setup**. In the Windows tools-included edition, the bundled tools are selected automatically; **Install Tools** only verifies them. In the regular edition, **Install Tools** downloads Micromamba, checks its SHA-256 hash, and installs CPU COLMAP, FFmpeg and FFprobe from conda-forge into Blender’s user data directory. This download requires Blender online access. Both paths run all three tools before reporting success. **Setup Details** shows progress and live output. **Open Setup Logs** opens the logs.
4. To use your own executables, set their paths under **Setup** and run **Check Setup**. You can also enter a tool directory containing the executable directly or under `bin/`. For an in-place COLMAP build the add-on also checks `build/src/colmap/exe/`. FFprobe is optional for playback FPS.

If Windows blocks or quarantines an executable, **Setup Details** includes the available Windows error. Check **Windows Security → Protection history** for Defender detections. On managed PCs, ask your administrator for approved builds and select them under **Setup**. For a bundled importer setup failure, open `importer-setup.log` from **Open Setup Logs**.

**Clear Setup Output** clears the panel's recent output and collapses Setup Details; it does not delete logs or uninstall tools. While setup is running, new output can still arrive. **Open Setup Logs** opens the setup job's log directory, not a reconstruction batch directory. `setup-status.log` is created before the job starts; `setup-diagnostics.log` records installation/verification success or failure. `install.log` exists only when Micromamba package installation starts. The Windows bundle instead produces `install-check-colmap.log`, `install-check-ffmpeg.log`, and `install-check-ffprobe.log` as verification reaches each executable.

On Windows, an unverified-publisher block for `colmap.exe` is separate from Micromamba's “Non-conda folder exists at prefix” error. Changing to Miniconda does not establish trust in the COLMAP executable. Keep Windows security enabled and use an approved compatible executable if the downloaded build is blocked. The installer preserves existing incomplete environment folders and chooses a fresh destination on retry.

The included COLMAP import path does not need the importer's optional Pillow, Pyntcloud, laspy, or lazrs packages. Those are relevant to other formats and features offered by that add-on. The add-on does not run pip or download software on enable.

## Use

1. **Globlender** defaults to `~/Videos/globlender`. **Input Folder** and **Output Folder** default to its `input_videos/` and `finished_tracks/` subfolders. Clear either field to restore its default. Projects saved with an earlier destination use their existing workspace.
2. Use **+** beside **Video List to process**. The picker starts in **Input Folder**, and you can browse to another folder if needed. External clips are copied into Input Folder. Files already there are selected in place. If a filename is taken, the new copy gets a numbered suffix. Only clips in the list are processed.
3. Leave **Auto-import completed clips** enabled if you want finished results imported automatically.
4. Choose **Auto GPU / CPU** or **CPU only**, and a mapping method. The default is incremental mapping.
5. Click **Run GLOMAP**. The FFmpeg/COLMAP pipeline runs in a separate process. Blender stays available while it runs. The sidebar shows the current stage for each clip and a progress bar when a frame or image count is available. Open **Live Terminal Output** to see recent tool output and how long the current command has been running. Use **Copy Recent Output** to share up to 100 recent lines, or **Open Logs Folder** for the full logs. You can switch tabs or hide the sidebar while the job runs.
6. Each completed clip imports into a separate collection named `GLOMAP | <video filename>`. Cameras, the animated camera, and scene points stay within that clip's collection hierarchy. Distortion warnings are always suppressed for imports made by this add-on.

Auto-import uses the scene where processing began. If you change the active scene, the results are kept on disk and can be imported manually. Switch to Object Mode before importing. Disabling the add-on, loading another blend file, or exiting Blender stops its running job.

## Output and safety

The default folders are created under Globlender as processing needs them. Input and output can be overridden independently. Existing projects from earlier versions can still be imported by selecting their finished clip folders. On the next run, an unlocked `04 SCENES/` folder is renamed to `output_tracks/` if that destination is free.

- `input_videos/`: default input folder. Clips are read directly from their original locations. Keep the original videos available until processing has finished.
- `output_tracks/`: extracted frames, working databases, step logs, and add-on batch logs.
- `finished_tracks/<video filename>/`: corrected images, `sparse/0/` BIN/TXT files, quality results, counts, logs, and completion marker.
- `failed/`: archived failed or previous working scenes.

When clips are selected, only those clips are processed. Clips with the same filename from different directories must be renamed or run in separate project folders.

Completed inputs with matching settings are skipped. Previously finished output is never overwritten silently. To rebuild a clip with changed settings, first move its existing finished folder out of `finished_tracks/`; the old working scene is archived automatically. Reimporting an already imported dataset keeps the existing collection rather than duplicating it. Remove that collection yourself if you intentionally want to import it again.

**Cancel Reconstruction** stops the active tool and its child processes. Completed clips and working logs are retained. A crash may leave a stale `output_tracks/.track.lock`. Confirm that no reconstruction is running before manually removing a stale lock.

On Linux and macOS, **Pause** suspends the active tool and holds the batch. **Resume** continues the same run. The icon next to each clip can hold that clip separately. A queued clip can be held while others run. Pausing the active clip holds the current tool and the batch until that clip is resumed. Windows still supports cancellation.

## Settings

Every source frame is extracted with FFmpeg passthrough timing: there is no FPS conversion or frame-count cap. The image longest edge defaults to 2000 pixels; change it under COLMAP Settings if needed. All frames are submitted for processing, and 100% of extracted frames must have a recovered camera pose before the script marks the result finished. This cannot force COLMAP to solve a blurred or unsuitable frame.

Advanced settings include CPU threads, matching overlap, feature count, camera model, fixed-lens assumption, minimum point count, and maximum reprojection error. Default quality gates also require at least 10 registered images, 100 3D points, and mean reprojection error no greater than 2 pixels. Pipeline command availability and flags are checked at runtime.

GPU acceleration is attempted for feature extraction and matching, with CPU retries after a failed GPU attempt. Mapping remains on CPU. Global mapping uses the integrated COLMAP commands and falls back to incremental mapping on reconstruction/quality failure. Standalone `glomap` is not required.

## Import and playback

- Default point display is **Mesh + Geometry Nodes**. Globlender creates the colored points directly from the saved track, and the Photogrammetry Importer loads the cameras. Point radius is adjustable.
- **OpenGL viewport points** remains available. That mode depends on the importer and Blender's graphics backend; use OpenGL if its overlay does not display.
- Camera animation and camera backgrounds can be enabled or disabled.
- If FFprobe and the source video are available, the first imported clip can set the scene's FPS, including fractional rates. A later clip with a different FPS does not silently change the existing scene FPS; a warning is shown. Use separate Blender scenes for clips needing different playback rates.
- All frames from variable-frame-rate videos are retained, but individual source timestamps are not used to retime animation. Playback timing for VFR footage is not guaranteed.

This reconstructs cameras and a sparse point cloud. It does not train Gaussian splats or generate a finished surface mesh.

## Compatibility and validation

The Python runner supports Linux, Windows and macOS. Automatic tool setup targets Linux x86-64 and ARM64, Windows x86-64, and Intel and Apple Silicon Macs. The private installation uses a CPU COLMAP build. To use a GPU build, point **Setup** to a compatible COLMAP executable and select GPU or Auto.

The install-from-disk ZIP includes the Photogrammetry Importer, but obtains FFmpeg and COLMAP during setup or uses tools you already have. Linux Blender 4.3.2 was used to test the ZIP and import pipeline. Native Windows and macOS execution still needs validation.

The package includes upstream importer releases `v2024.01.18` and `v2026.02.16`. Setup selects the older importer for Blender 4.0–4.3 and the newer release for Blender 4.4 and later. Blender 4.4/4.5/5.x and GPU-specific behaviour have not been tested here. An already installed importer is reused; if it is incompatible with your Blender version, remove it before running setup again.

## Third-party notices

Photogrammetry Importer is a separate project by Sebastian Bullinger:
https://github.com/SBCV/Blender-Addon-Photogrammetry-Importer

Its original release ZIPs are redistributed unmodified under the MIT license. The complete copyright notice and license are in `bundled/PHOTOGRAMMETRY_IMPORTER_LICENSE.txt`. Upstream releases and checksums are recorded in `bundled/NOTICE.md`. The main add-on source and its bundled pipeline are included in this package.
