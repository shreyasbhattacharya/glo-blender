"""Release routing and failure-log regressions; no Blender dependency."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('glomap_test')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
from glomap_test import toolchain, job_runner


class ReleaseTests(unittest.TestCase):
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
