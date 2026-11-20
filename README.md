# Blender GLOMAP — Video to 3D

Turn a video into an **animated camera and a sparse 3D point cloud in Blender**.
The add-on processes your footage and imports the result into its own collection.
It does not create Gaussian splats or a finished surface mesh.

## 1. Download

**[Download v0.3.16 →](https://github.com/shreyasbhattacharya/glo-blender/releases/tag/v0.3.16)**

Under **Assets**, choose one:

| Your computer | ZIP to download | Testing status |
| --- | --- | --- |
| Linux | `blender_glomap-v0.3.16.zip` | Tool installation tested on the development machine |
| Windows x64 | `blender_glomap-v0.3.16-windows-x64.zip` | Bundled tools plus online repair; native Windows testing pending |
| macOS | `blender_glomap-v0.3.16.zip` | Testing pending; manual tool setup recommended |

**Keep the ZIP zipped.** You only need one. Ignore GitHub's **Source code** downloads.
The `.sha256` files are optional download checksums, not add-ons.

### What's new in 0.3.16

- **Adding clips:** fixed the `NoneType has no attribute 'parent'` crash when adding videos to a new Blender file.
- **Camera backgrounds:** imported reference images now use 100% opacity and have their display enabled.
- **Windows tool repair:** if bundled COLMAP fails to load its DLLs, **Install / Repair Tools** downloads and verifies a separate conda-forge CPU environment. Future runs automatically select the verified environment.
- **Updates:** Python submodules reload together, preventing a new panel from using old processing code.
- **Diagnostics:** Windows DLL failures include dependency reports; setup shows the add-on version and importer status separately.

**Updating:** remove the old **Video to 3D — Blender GLOMAP** add-on in Preferences,
close and reopen Blender, then install the new ZIP. Confirm **0.3.16** appears under Setup.
For an existing reconstruction, enable **Camera background images** under import
options and use **Import Output to Project** on the same output folder to refresh
background visibility without duplicating the reconstruction.

## 2. Install in Blender

1. Open **Edit → Preferences → Add-ons → Install from Disk**. In some Blender versions, this is inside the dropdown menu.
2. Select your downloaded ZIP and enable **Video to 3D — Blender GLOMAP**.
3. In the 3D View, press **N** and open the **Video to 3D** tab.
4. Click **Set Up Importer** to enable the included Photogrammetry Importer. No separate download is needed.
5. Open **Setup**:
   - **Linux:** click **Install / Repair Tools** and wait for verification. Internet access is required.
   - **Windows tools-included ZIP:** click **Check Setup**. If COLMAP fails to load, click **Install / Repair Tools** with internet access.
   - **macOS or your own tools:** follow the [manual installation instructions](docs/technical-guide.md#manual-tools-recommended-on-other-platforms-while-testing-is-pending), select their paths under Setup, then click **Check Setup**.

## 3. Process your video

1. Click **+** beside the video list and choose a clip.
2. Leave **Automatically Import Output** enabled.
3. Keep the default settings for your first test and click **Run GLOMAP**.
4. Watch progress in the sidebar. Finished results appear in a collection named **`GLOMAP | your-video`**.

The animated camera and point cloud remain visible; individual frame cameras are hidden by default.
Stay in the same Blender scene and use **Object Mode** for importing.

**Where are my results?** By default, in `~/Videos/globlender/finished_tracks/`. Change this using **Output Folder**.

**Already processed a clip?** Completed results are reused when the source and settings match.
To import a saved result, click **Import Output to Project** and select the completed video folder containing `images/` and `sparse/`.

## Need help?

| Problem | What to do |
| --- | --- |
| Tool setup fails | Open **Setup Details → Open Setup Logs** and share the error and logs. |
| Windows error `0xC0000135` | Click **Install / Repair Tools**. If repair fails, share `install.log`, `setup-diagnostics.log`, and `*.dlls.log` from Open Setup Logs. |
| COLMAP error remains after Set Up Importer | Tool setup and importer setup are independent. Check **Importer: enabled**; retained tool errors do not mean the importer failed. |
| Windows blocks `colmap.exe` | Check **Windows Security → Protection history**. Included tools still follow Windows security policies. |
| Importer setup fails | Share `importer-setup.log` from the setup logs folder. |
| A finished track will not import | Try **Import Output to Project**. If created, share `import-error.log` from the video's finished folder. |
| No `install.log` | This is normal when no package installation ran. Check `setup-status.log`, `setup-diagnostics.log`, and any `install-check-*.log` files. |

### Windows Security

**Keep Windows Security enabled.** The add-on does not require disabling antivirus,
SmartScreen, or Blender security protections, and does not change those settings.
Bundled tools run as normal subprocesses. If a tool is blocked or quarantined,
check **Windows Security → Protection history** for the specific detection.
Compatibility with every antivirus or organization policy cannot be guaranteed.

**Allow Online Access** in Blender Preferences is needed only for downloading or
repairing tools; it is separate from Windows Security. Verification of working
bundled tools and processing with installed tools can run offline.

### Testing status

This is a **prerelease**. A user has reported successful COLMAP-project import and
animated cameras on Windows. The latest clip-add and background-display fixes
passed automated tests and Blender integration tests on Linux; they have not yet
been independently verified on Windows. Windows package dependency resolution
and bundled DLL imports were checked. macOS testing remains pending.

## More information

- [Manual installation commands for macOS, Windows, and Linux](docs/technical-guide.md#manual-tools-recommended-on-other-platforms-while-testing-is-pending)
- [Settings, output folders, and playback details](docs/technical-guide.md#use)
- [Build release ZIPs — for contributors](docs/technical-guide.md#building-releases)
- [Windows tool sources and notices](bundled/WINDOWS_TOOLS.md)

## Credits

Built with [COLMAP](https://github.com/colmap/colmap), [FFmpeg](https://ffmpeg.org/), and [Photogrammetry Importer](https://github.com/SBCV/Blender-Addon-Photogrammetry-Importer) by Sebastian Bullinger.
See [importer notices](bundled/NOTICE.md) and [license](bundled/PHOTOGRAMMETRY_IMPORTER_LICENSE.txt).
