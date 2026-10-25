import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from desktop import DesktopServer, available_port, child_environment, check_native_prerequisites


class DesktopTests(unittest.TestCase):
    def test_missing_native_runtime_has_official_install_instructions(self):
        with patch('desktop.sys.platform', 'win32'), patch('desktop.ctypes.WinDLL', side_effect=OSError, create=True):
            with self.assertRaisesRegex(RuntimeError, 'https://aka.ms/vc14/vc_redist.x64.exe'):
                check_native_prerequisites()

    def test_backend_is_isolated_loopback_and_does_not_open_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            server = DesktopServer(python=Path(folder) / 'python.exe', app_directory=folder, data_directory=folder)
            command = server.command()
            self.assertIn('-I', command)
            self.assertIn('-B', command)
            self.assertIn('--server.address=127.0.0.1', command)
            self.assertIn('--server.headless=true', command)
            self.assertIn('--server.fileWatcherType=none', command)
            self.assertIn('--client.toolbarMode=viewer', command)
            self.assertNotEqual(server.port, 8517)
            server.stop()

    def test_inherited_python_paths_do_not_escape_portable_runtime(self):
        with patch.dict(os.environ, {'PYTHONPATH': 'unrelated-python', 'PYTHONHOME': 'unrelated-home'}):
            env = child_environment()
        self.assertNotIn('PYTHONHOME', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['PYTHONNOUSERSITE'], '1')
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_free_port_and_missing_app_files(self):
        port = available_port()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
        with tempfile.TemporaryDirectory() as folder:
            server = DesktopServer(python=Path(folder) / 'missing.exe', app_directory=folder, data_directory=folder)
            with self.assertRaisesRegex(RuntimeError, 'App files are missing'):
                server.start()
            server.stop()
            server.stop()

    def test_closed_window_never_starts_a_late_server(self):
        with tempfile.TemporaryDirectory() as folder:
            server = DesktopServer(python=sys.executable, app_directory=folder, data_directory=folder)
            server.stop()
            server.start()
            self.assertIsNone(server.process)


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed')
class DesktopServerTests(unittest.TestCase):
    def test_server_starts_and_closing_stops_its_port(self):
        with tempfile.TemporaryDirectory() as folder:
            server = DesktopServer(data_directory=folder)
            try:
                server.start()
                self.assertTrue(server.wait_ready(timeout=30))
                with socket.create_connection(('127.0.0.1', server.port), timeout=1):
                    pass
            finally:
                server.stop()
            self.assertIsNotNone(server.process.poll())
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', server.port), timeout=1)
