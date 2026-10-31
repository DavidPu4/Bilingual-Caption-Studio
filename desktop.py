"""Windows desktop entry point. The UI shell owns an isolated local app server."""
from __future__ import annotations

import argparse
import atexit
import ctypes
from ctypes import wintypes
import html
import http.client
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

APP_NAME = 'Bilingual Caption Studio'
VERSION = '1.1.0'


def application_paths():
    if getattr(sys, 'frozen', False):
        root = Path(sys.executable).resolve().parent
        return root / 'runtime' / 'python.exe', root / 'app'
    return Path(sys.executable).resolve(), Path(__file__).resolve().parent


def user_data_directory():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    directory = base / 'BilingualCaptionStudio'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def child_environment():
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               PYINSTALLER_RESET_ENVIRONMENT='1', STREAMLIT_BROWSER_GATHER_USAGE_STATS='false')
    return env


def check_native_prerequisites():
    if sys.platform != 'win32':
        return
    for library in ('msvcp140.dll', 'vcruntime140_threads.dll'):
        try:
            ctypes.WinDLL(library)
        except OSError:
            raise RuntimeError('Install the latest Microsoft Visual C++ x64 Redistributable, then reopen the app. '
                               'Official download: https://aka.ms/vc14/vc_redist.x64.exe') from None


class WindowsJob:
    """Kill only this app's server and descendants when the desktop exits."""
    def __init__(self):
        self.handle = None
        if sys.platform != 'win32':
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        class BasicLimit(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t),
                        ('maximum', ctypes.c_size_t), ('process_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class ExtendedLimit(ctypes.Structure):
            _fields_ = [('basic', BasicLimit), ('io', Counters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        info = ExtendedLimit()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if handle and kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle, self.kernel = handle, kernel
        elif handle:
            kernel.CloseHandle(handle)

    def attach(self, process):
        if not self.handle:
            return False
        handle = self.kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            return False
        try:
            return bool(self.kernel.AssignProcessToJobObject(self.handle, handle))
        finally:
            self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class DesktopServer:
    def __init__(self, python=None, app_directory=None, data_directory=None):
        default_python, default_app = application_paths()
        self.python = Path(python or default_python)
        self.app_directory = Path(app_directory or default_app)
        self.data_directory = Path(data_directory or user_data_directory())
        self.port = available_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.process = None
        self.log_file = None
        self.job = None
        self.in_job = False
        self.closed = threading.Event()
        self.lock = threading.Lock()

    def command(self):
        prefix = [str(self.python), '-I', '-B', '-m', 'streamlit']
        if (sys.platform == 'win32' and not getattr(sys, 'frozen', False)
                and self.python == Path(sys.executable).resolve() and sys.prefix != sys.base_prefix):
            # Windows venv python.exe is a redirector. Launch the real interpreter so it cannot
            # spawn an unowned grandchild before we attach the process to its cleanup job.
            packages = Path(sys.prefix) / 'Lib' / 'site-packages'
            code = f'import site; site.addsitedir({str(packages)!r}); from streamlit.web.cli import main; main()'
            prefix = [str(Path(sys._base_executable)), '-I', '-B', '-c', code]
        return [*prefix, 'run', str(self.app_directory / 'app.py'),
                '--server.address=127.0.0.1', f'--server.port={self.port}',
                '--server.headless=true', '--server.fileWatcherType=none',
                '--browser.gatherUsageStats=false', '--client.toolbarMode=viewer']

    def start(self):
        with self.lock:
            if self.closed.is_set():
                return
            if not self.python.is_file() or not (self.app_directory / 'app.py').is_file():
                raise RuntimeError('App files are missing. Extract the entire desktop ZIP before opening the app.')
            logs = self.data_directory / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            self.log_path = logs / f'server-{self.port}.log'
            self.log_file = self.log_path.open('ab')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
            # A frozen launcher must not pass its private DLL search directory to external Python.
            frozen = sys.platform == 'win32' and getattr(sys, 'frozen', False)
            if frozen:
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                self.process = subprocess.Popen(self.command(), cwd=self.app_directory, env=child_environment(),
                                                stdout=self.log_file, stderr=subprocess.STDOUT, **options)
            finally:
                if frozen:
                    ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))
            self.job = WindowsJob()
            self.in_job = self.job.attach(self.process)

    def wait_ready(self, timeout=90):
        deadline = time.monotonic() + timeout
        # Plain loopback HTTP needs neither system proxies nor a TLS/certificate context.
        while time.monotonic() < deadline:
            if self.closed.is_set():
                return False
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError(f'The app server could not start. See {getattr(self, "log_path", "the logs folder")}.')
            try:
                connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=1)
                try:
                    connection.request('GET', '/_stcore/health')
                    response = connection.getresponse()
                    if response.status == 200 and response.read().strip() == b'ok':
                        return True
                finally:
                    connection.close()
            except (OSError, http.client.HTTPException):
                pass
            self.closed.wait(.2)
        raise RuntimeError(f'Startup took too long. See {self.log_path}.')

    def stop(self):
        self.closed.set()
        with self.lock:
            if self.job:
                self.job.close()
            if self.process and self.process.poll() is None:
                if sys.platform == 'win32' and not self.in_job:
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                elif not self.in_job:
                    import signal
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            # Job termination and socket teardown can complete just after the parent exits.
            if self.process:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(('127.0.0.1', self.port), timeout=.1):
                            pass
                    except OSError:
                        break
                    time.sleep(.05)
            if self.log_file:
                self.log_file.close()
                self.log_file = None


SPLASH = '''<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#f8fafc;color:#0f172a;font:17px 'Segoe UI',sans-serif;display:grid;place-items:center;height:100vh}
main{text-align:center;max-width:600px;padding:40px}.logo{background:#0d9488;border-radius:22px;color:white;font-size:40px;padding:20px;display:inline-block}
h1{font-size:32px}p{color:#64748b}.dots{color:#0d9488;letter-spacing:10px;font-size:30px;animation:pulse 1s infinite alternate}
@keyframes pulse{to{opacity:.25}}</style></head><body><main><div class="logo">中 ⇄ A</div>
<h1>Bilingual Caption Studio</h1><p>Starting your desktop workspace...</p><div class="dots">•••</div></main></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=APP_NAME + ' desktop launcher')
    parser.add_argument('--self-test', action='store_true', help='Check the bundled server, then close it without opening a window')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    server = DesktopServer()
    atexit.register(server.stop)
    logs = server.data_directory / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=logs / 'desktop.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    if args.self_test:
        result = {'ok': False}
        try:
            check_native_prerequisites()
            server.start()
            result.update(ok=server.wait_ready(), port=server.port, pid=server.process.pid,
                          python=str(server.python), app=str(server.app_directory), job_owned=server.in_job)
        except Exception as exc:
            logging.exception('Desktop self-test failed')
            result['error'] = str(exc)
        finally:
            server.stop()
        result['server_stopped'] = server.process is None or server.process.poll() is not None
        if args.report:
            args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        if sys.stdout:
            print(json.dumps(result, indent=2))
        return 0 if result['ok'] and result['server_stopped'] else 1
    try:
        check_native_prerequisites()
        import webview
        if sys.platform == 'win32':
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DavidPu.BilingualCaptionStudio')
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['ALLOW_FILE_URLS'] = False
        window = webview.create_window(APP_NAME, html=SPLASH, width=1280, height=900,
                                        min_size=(960, 640), text_select=True,
                                        background_color='#f8fafc')
        window.events.closed += server.stop

        def launch():
            try:
                server.start()
                if server.wait_ready():
                    window.load_url(server.url)
            except Exception as exc:
                logging.exception('Desktop startup failed')
                if not server.closed.is_set():
                    window.load_html('<html><body style="font:18px Segoe UI;padding:50px">'
                                     '<h1>Unable to start the app</h1><p>' + html.escape(str(exc)) + '</p></body></html>')
        webview.start(launch, gui='edgechromium' if sys.platform == 'win32' else None,
                      private_mode=True, storage_path=str(server.data_directory / 'webview'))
    except Exception as exc:
        logging.exception('Desktop window failed')
        message = (f'{APP_NAME} could not open.\n\n{exc}\n\n'
                   f'Logs: {logs}\n\nWindows requires the Microsoft Edge WebView2 Runtime. '
                   'See the desktop README for its official download link.')
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
        elif sys.stderr:
            print(message, file=sys.stderr)
        return 1
    finally:
        server.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
