# Blender GLOMAP — Video to 3D

Turn a video into an **animated camera and a sparse 3D point cloud in Blender**.
The add-on processes your footage and imports the result into its own collection.
It does not create Gaussian splats or a finished surface mesh.

## 1. Download

**[Open the downloads page →](https://github.com/shreyasbhattacharya/glo-blender/releases/tag/v0.3.13)**

Under **Assets**, choose one:

| Your computer | ZIP to download | Testing status |
| --- | --- | --- |
| Linux | `blender_glomap-v0.3.13.zip` | Install Tools tested on the development machine |
| Windows x64 | `blender_glomap-v0.3.13-windows-x64.zip` | Tools included; Windows testing pending |
| macOS | `blender_glomap-v0.3.13.zip` | Testing pending; manual tool setup recommended |

**Keep the ZIP zipped.** You only need one. Ignore GitHub's **Source code** downloads.
The `.sha256` files are optional download checksums, not add-ons.

## 2. Install in Blender

1. Open **Edit → Preferences → Add-ons → Install from Disk**. In some Blender versions, this is inside the dropdown menu.
2. Select your downloaded ZIP and enable **Video to 3D — Blender GLOMAP**.
3. In the 3D View, press **N** and open the **Video to 3D** tab.
4. Click **Set Up Importer** to enable the included Photogrammetry Importer. No separate download is needed.
5. Open **Setup**:
   - **Linux:** click **Install Tools** and wait for verification. Internet access is required.
   - **Windows tools-included ZIP:** click **Check Setup**. The included tools are selected automatically.
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
| Windows blocks `colmap.exe` | Check **Windows Security → Protection history**. Included tools still follow Windows security policies. |
| Importer setup fails | Share `importer-setup.log` from the setup logs folder. |
| A finished track will not import | Try **Import Output to Project**. If created, share `import-error.log` from the video's finished folder. |
| No `install.log` | This is normal when no package installation ran. Check `setup-status.log`, `setup-diagnostics.log`, and any `install-check-*.log` files. |

**This is a testing release.** Windows and macOS testing is pending. The reported Windows Phot “Invalid colmap model / workspace” error is not confirmed resolved.

## More information

- [Manual installation commands for macOS, Windows, and Linux](docs/technical-guide.md#manual-tools-recommended-on-other-platforms-while-testing-is-pending)
- [Settings, output folders, and playback details](docs/technical-guide.md#use)
- [Build release ZIPs — for contributors](docs/technical-guide.md#building-releases)
- [Windows tool sources and notices](bundled/WINDOWS_TOOLS.md)

## Credits

Built with [COLMAP](https://github.com/colmap/colmap), [FFmpeg](https://ffmpeg.org/), and [Photogrammetry Importer](https://github.com/SBCV/Blender-Addon-Photogrammetry-Importer) by Sebastian Bullinger.
See [importer notices](bundled/NOTICE.md) and [license](bundled/PHOTOGRAMMETRY_IMPORTER_LICENSE.txt).
