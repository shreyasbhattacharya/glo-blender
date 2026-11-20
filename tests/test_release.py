"""Release routing and failure-log regressions; no Blender dependency."""
from pathlib import Path
import io
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('glomap_test')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
from glomap_test import toolchain, job_runner, reconstruction
from glomap_test import windows_dll
spec = importlib.util.spec_from_file_location('release_package', ROOT/'scripts'/'package.py')
release_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_package)


def pe_image(*imports):
    """Small PE32+ fixture with a real section-backed import table."""
    data = bytearray(2048)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, 0x8664, 1)
    struct.pack_into('<H', data, 148, 240)
    struct.pack_into('<H', data, 152, 0x20B)
    struct.pack_into('<I', data, 260, 16)
    struct.pack_into('<II', data, 272, 0x1000, (len(imports) + 1) * 20)
    struct.pack_into('<IIII', data, 400, 1536, 0x1000, 1536, 512)
    for index, name in enumerate(imports):
        start = 1024 + index * 128
        struct.pack_into('<IIIII', data, 512 + index * 20, 0, 0, 0,
                         0x1000 + start - 512, 0)
        encoded = name.encode('ascii') + b'\0'
        data[start:start + len(encoded)] = encoded
    return bytes(data)


class ReleaseTests(unittest.TestCase):
    def test_clip_copy_job_without_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'clip.mp4'
            source.write_bytes(b'clip data')
            destination = root/'input'
            destination.mkdir()
            job = job_runner.Job()

            def copy():
                job.results.append(job_runner.copy_input_clip(source, destination, job.check_cancelled))
                job.message('Added 1 clip(s) to Input Folder')
                return 0

            job.launch(copy, None)
            job.thread.join()
            self.assertFalse(job.poll())
            self.assertEqual(job.returncode, 0)
            self.assertEqual(job.status, 'Added 1 clip(s) to Input Folder')
            self.assertEqual(job.results, [destination/'clip.mp4'])
            self.assertEqual(job.results[0].read_bytes(), source.read_bytes())
            self.assertIsNone(job.log_path)

    def test_job_without_log_reports_original_failure(self):
        job = job_runner.Job()

        def fail():
            raise OSError('Could not copy video')

        job.launch(fail, None)
        job.thread.join()
        self.assertFalse(job.poll())
        self.assertEqual(job.returncode, 1)
        self.assertEqual(job.status, 'Failed: Could not copy video')

    def test_log_write_failure_does_not_hide_worker_error(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp)/'job.log'
            job = job_runner.Job()

            def fail():
                log.unlink()
                log.mkdir()
                raise RuntimeError('Original failure')

            job.launch(fail, log)
            job.thread.join()
            self.assertFalse(job.poll())
            self.assertEqual(job.status, 'Failed: Original failure')

    def test_bootstrap_gets_runtime_without_colmap_dlls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'bundle'
            source.mkdir()
            dest = root/'bootstrap'
            dest.mkdir()
            for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
                         'vcruntime140_threads.dll', 'Qt6Core.dll'):
                (source/name).write_bytes(name.encode())
            with patch.object(toolchain, 'target', return_value='win-64'), \
                    patch.object(toolchain, 'bundled_tools', return_value=source), \
                    patch.object(toolchain, 'bundled_executable', return_value=source/'colmap.exe'):
                toolchain.prepare_bootstrap_runtime(dest)
            self.assertEqual((dest/'msvcp140.dll').read_bytes(), b'msvcp140.dll')
            self.assertTrue((dest/'vcruntime140_threads.dll').exists())
            self.assertFalse((dest/'Qt6Core.dll').exists())

    def test_bundled_loader_failure_repairs_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(toolchain, 'bundled_tools', return_value=root/'bundle'), \
                    patch.object(toolchain, 'bundled_executable', return_value=root/'colmap.exe'), \
                    patch.object(toolchain.Pipeline, 'run_command',
                                 side_effect=reconstruction.WindowsLoaderError('missing.dll')), \
                    patch.object(toolchain, 'install_environment', return_value=0) as repair:
                self.assertEqual(toolchain.install(job_runner.Job(), root, root/'logs'), 0)
                repair.assert_called_once()
                self.assertIn('Bundled tool loader failure: missing.dll',
                              (root/'logs/setup-diagnostics.log').read_text())

    def test_offline_loader_failure_does_not_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(toolchain, 'bundled_tools', return_value=root/'bundle'), \
                    patch.object(toolchain, 'bundled_executable', return_value=root/'colmap.exe'), \
                    patch.object(toolchain.Pipeline, 'run_command',
                                 side_effect=reconstruction.WindowsLoaderError('missing.dll')), \
                    patch.object(toolchain, 'install_environment') as repair:
                with self.assertRaisesRegex(RuntimeError, 'Allow Online Access'):
                    toolchain.install(job_runner.Job(), root, root/'logs', allow_download=False)
                repair.assert_not_called()

    def test_cancellation_and_other_failures_do_not_trigger_repair(self):
        for failure in (reconstruction.Cancelled(), RuntimeError('access denied')):
            with self.subTest(failure=type(failure)), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with patch.object(toolchain, 'bundled_tools', return_value=root/'bundle'), \
                        patch.object(toolchain, 'bundled_executable', return_value=root/'colmap.exe'), \
                        patch.object(toolchain.Pipeline, 'run_command', side_effect=failure), \
                        patch.object(toolchain, 'install_environment') as repair:
                    with self.assertRaises(type(failure)):
                        toolchain.install(job_runner.Job(), root, root/'logs')
                    repair.assert_not_called()

    def test_repaired_environment_wins_over_bundle_and_system_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(toolchain, 'target', return_value='win-64'), \
                    patch.object(toolchain, 'bundled_tools', return_value=root/'bundle'), \
                    patch.object(toolchain.shutil, 'which', return_value='/custom/colmap.exe'):
                dest = toolchain.prefix(root)
                (dest/'Library/bin').mkdir(parents=True)
                (dest/'.glomap-ready').touch()
                for name in toolchain.CHECKS:
                    executable = dest/'Library/bin'/f'{name}.exe'
                    executable.touch()
                    self.assertEqual(toolchain.resolve_tool(name, '', root), str(executable))
                self.assertEqual(toolchain.resolve_tool('colmap', '/custom/colmap.exe', root),
                                 '/custom/colmap.exe')

    def test_managed_environment_does_not_mix_bundled_dlls_or_qt_plugins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root/'bundle'
            (bundle/'colmap/plugins').mkdir(parents=True)
            (bundle/'colmap/Qt6Core.dll').touch()
            with patch.object(toolchain, 'target', return_value='win-64'), \
                    patch.object(toolchain, 'bundled_tools', return_value=bundle), \
                    patch.dict(toolchain.os.environ, {'PATH': '/system',
                               'QT_PLUGIN_PATH': '/old/plugins', 'QT_QPA_PLATFORM_PLUGIN_PATH': '/old'}):
                dest = root/'new-environment'
                env = toolchain.environment(root, install_prefix=dest)
                self.assertEqual(env['PATH'].split(toolchain.os.pathsep)[0], str(dest/'Library/bin'))
                self.assertNotIn(str(bundle), env['PATH'])
                self.assertNotIn('QT_PLUGIN_PATH', env)
                self.assertNotIn('QT_QPA_PLATFORM_PLUGIN_PATH', env)

    def test_verified_repair_is_reused_without_running_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dest = toolchain.prefix(root)
            dest.mkdir(parents=True)
            (dest/'.glomap-ready').touch()
            with patch.object(toolchain, 'bundled_tools', return_value=root/'bundle'), \
                    patch.object(toolchain, 'bundled_executable') as bundled, \
                    patch.object(toolchain, 'install_environment', return_value=0) as managed:
                self.assertEqual(toolchain.install(job_runner.Job(), root, root/'logs',
                                                  allow_download=False), 0)
                bundled.assert_not_called()
                managed.assert_called_once()

    def test_loader_failure_writes_missing_dll_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root/'colmap.exe'
            executable.write_bytes(pe_image('vcomp140.dll'))
            job = job_runner.Job()
            child = MagicMock()
            child.poll.return_value = 0xC0000135
            runner = reconstruction.Pipeline(job, root, {}, {'colmap': str(executable)},
                                             {'PATH': ''}, root)
            with patch.object(reconstruction, 'os', types.SimpleNamespace(name='nt')), \
                    patch.object(reconstruction.subprocess, 'Popen', return_value=child):
                with self.assertRaisesRegex(reconstruction.WindowsLoaderError, 'Missing DLLs: vcomp140.dll'):
                    runner.run_command('colmap', ['help'], root/'install-check-colmap.log')
            report = (root/'install-check-colmap.dlls.log').read_text()
            self.assertIn('MISSING: vcomp140.dll (required by colmap.exe)', report)
            self.assertIn('DLL diagnostics:', (root/'install-check-colmap.log').read_text())

    def test_dependency_report_finds_transitive_missing_dll(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'colmap.exe').write_bytes(pe_image('ceres.dll'))
            (root/'CERES.dll').write_bytes(pe_image('VCOMP140.DLL', 'api-ms-win-crt-runtime-l1-1-0.dll'))
            lines, missing = windows_dll.dependency_report(root/'colmap.exe', {
                'SystemRoot': str(root/'Windows'), 'PATH': '',
            })
            self.assertEqual(missing, ['vcomp140.dll'])
            self.assertIn('MISSING: vcomp140.dll (required by CERES.dll)', lines)
            (root/'vcomp140.dll').write_bytes(pe_image())
            self.assertEqual(windows_dll.dependency_report(root/'colmap.exe', {'PATH': ''})[1], [])

    def test_archive_audit_rejects_missing_transitive_runtime(self):
        data = io.BytesIO()
        with ZipFile(data, 'w') as bundle:
            base = 'blender_glomap/bundled/tools/windows-x64/colmap/bin/'
            bundle.writestr(base + 'colmap.exe', pe_image('ceres.dll'))
            bundle.writestr(base + 'ceres.dll', pe_image('vcomp140.dll'))
            with self.assertRaisesRegex(ValueError, 'ceres.dll requires missing DLLs: vcomp140.dll'):
                release_package.audit_windows_bundle(bundle)
            bundle.writestr(base + 'vcomp140.dll', pe_image('kernel32.dll'))
            release_package.audit_windows_bundle(bundle)

    def test_pe_parser_rejects_corrupt_binary(self):
        with self.assertRaises(ValueError):
            windows_dll.pe_imports(b'MZ' + b'\0' * 10)
        data = bytearray(pe_image('vcomp140.dll'))
        struct.pack_into('<I', data, 524, 0xFFFFFFFF)
        with self.assertRaisesRegex(ValueError, 'Invalid PE RVA'):
            windows_dll.pe_imports(data)

    def test_windows_runtime_complete_and_x64(self):
        files = release_package.runtime_files()
        self.assertTrue(release_package.REQUIRED_RUNTIME <= {p.name for p in files})

    def test_windows_packaging_rejects_missing_runtime_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(release_package, 'RUNTIME', root):
                with self.assertRaisesRegex(ValueError, 'Missing Windows runtime DLLs'):
                    release_package.build(root/'output', root/'colmap.zip', root/'ffmpeg.zip')
            self.assertFalse((root/'output').exists())

    def test_windows_packaging_rejects_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in release_package.REQUIRED_RUNTIME:
                data = bytearray(70)
                data[:2] = b'MZ'
                data[60:64] = (64).to_bytes(4, 'little')
                data[64:] = b'PE\0\0\x64\xaa'  # ARM64, not AMD64
                (root/name).write_bytes(data)
            with patch.object(release_package, 'RUNTIME', root):
                with self.assertRaisesRegex(ValueError, 'Expected an x64 Windows DLL'):
                    release_package.runtime_files()

    def test_bundled_paths_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for tool in toolchain.CHECKS:
                folder = base/('colmap' if tool == 'colmap' else 'ffmpeg')/'bin'
                folder.mkdir(parents=True, exist_ok=True)
                (folder/f'{tool}.exe').write_bytes(b'MZ')
            with patch.object(toolchain, 'bundled_tools', return_value=base):
                for tool in toolchain.CHECKS:
                    self.assertTrue(toolchain.resolve_tool(tool, '', base).endswith(f'{tool}.exe'))
                with patch.object(toolchain.shutil, 'which', return_value='/custom/colmap.exe'):
                    self.assertEqual(toolchain.resolve_tool('colmap', '/custom/colmap.exe', base),
                                     '/custom/colmap.exe')

    def test_missing_bundle_does_not_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(toolchain, 'bundled_tools', return_value=root), \
                    patch.object(toolchain, 'install_environment') as network:
                with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
                    toolchain.install(job_runner.Job(), root, root/'logs')
                network.assert_not_called()
                self.assertIn('Failed:', (root/'logs/setup-diagnostics.log').read_text())

    def test_job_failure_logged_without_ui_poll(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp)/'setup-status.log'
            job = job_runner.Job()
            def fail():
                raise RuntimeError('verification failed')
            job.launch(fail, log)
            job.thread.join()
            self.assertIn('verification failed', log.read_text())


if __name__ == '__main__':
    unittest.main()
