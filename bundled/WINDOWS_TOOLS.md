# Windows tools bundle

The Windows x64 release retains the upstream archive layouts, DLLs, plugins,
licenses and documentation. Executables run as subprocesses, not Blender Python
modules. Native Windows verification is pending.

The corrected local bundle also includes Microsoft Visual C++ v14 x64 runtime
14.52.37029.0 beside `colmap.exe`, including the OpenMP runtime `vcomp140.dll`.
The original upstream COLMAP ZIP omitted these DLLs, causing Windows status
`0xC0000135` on systems without the runtime. App-local deployment does not need
administrator privileges or a system-wide runtime installation.

Runtime source: https://aka.ms/vc14/vc_redist.x64.exe
SHA-256: `9c02c9ed120f671b900e6b9471d890e3b2414117a7e53f4ae2e855466fa2b7c9`.
The Microsoft license page is included as `MICROSOFT_VC_RUNTIME_LICENSE.html`.
Recreate the local runtime files with `python scripts/prepare_windows_runtime.py`
(requires `cabextract`). The download is hash-pinned because Microsoft's URL rolls forward.

Tool sources retained from version 0.3.13 in 0.3.15:

| Tool | Source | SHA-256 of downloaded ZIP |
| --- | --- | --- |
| COLMAP 4.2.1, non-CUDA | https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-nocuda.zip | `c493d88cb4a43f21afba42cb55bf786e5434a0420e73db0ba1454c5d8b3686ae` |
| FFmpeg/FFprobe 9.0.2 essentials | https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip | `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba` |

Both hashes were compared with publisher-provided checksums. The FFmpeg URL
is a rolling release URL: verify the hash before reproducing this release.
The local 0.3.15 rebuild reuses the tool files from the existing local ZIP;
`sources.json` records the hashes of the repacked input archives used for this build.
FFmpeg's included README identifies its source revision and build configuration;
its included LICENSE is GPLv3. COLMAP source: https://github.com/colmap/colmap/tree/4.2.1.
FFmpeg source revision: https://github.com/FFmpeg/FFmpeg/commit/946fcce07b.

Windows Security policy still applies to bundled executables. This package does
not change security settings or require disabling protection.

Version 0.3.14 validates load-time PE imports for every bundled EXE and DLL,
including Qt plugins, against bundled files and Windows system libraries. On a
Windows loader failure, `*.dlls.log` records the installed dependency paths and
missing names without loading third-party DLLs into Blender. Static validation
does not replace a native Windows launch test.

Version 0.3.15 adds online repair: if bundled tools exit with a Windows DLL loader
error, Install / Repair Tools installs and verifies an independent conda-forge CPU
environment. Original bundled failure logs are preserved as `bundled-check-*`.
Only a successfully verified environment is selected for subsequent checks and
reconstructions. Its DLL and Qt plugin paths are kept separate from the bundle.
Add-on updates also reload Python submodules, avoiding mixed old/new pipeline code.
