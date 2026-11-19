# Windows tools bundle

The Windows x64 release retains the upstream archive layouts, DLLs, plugins,
licenses and documentation. Executables run as subprocesses, not Blender Python
modules. Native Windows verification is pending.

Sources used for version 0.3.13:

| Tool | Source | SHA-256 of downloaded ZIP |
| --- | --- | --- |
| COLMAP 4.2.1, non-CUDA | https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-nocuda.zip | `c493d88cb4a43f21afba42cb55bf786e5434a0420e73db0ba1454c5d8b3686ae` |
| FFmpeg/FFprobe 9.0.2 essentials | https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip | `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba` |

Both hashes were compared with publisher-provided checksums. The FFmpeg URL
is a rolling release URL: verify the hash before reproducing this release.
FFmpeg's included README identifies its source revision and build configuration;
its included LICENSE is GPLv3. COLMAP source: https://github.com/colmap/colmap/tree/4.2.1.
FFmpeg source revision: https://github.com/FFmpeg/FFmpeg/commit/946fcce07b.

Windows Security policy still applies to bundled executables. This package does
not change security settings or require disabling protection.
