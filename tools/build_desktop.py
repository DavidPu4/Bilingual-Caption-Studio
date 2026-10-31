"""Build a self-contained Windows folder/ZIP without freezing the ML backend."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop import APP_NAME, VERSION


def make_icon(path):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 252, 252), radius=52, fill='#0f766e')
    draw.rounded_rectangle((78, 88, 218, 196), radius=22, fill='#99f6e4')
    draw.polygon([(172, 184), (198, 220), (198, 180)], fill='#99f6e4')
    draw.rounded_rectangle((38, 40, 184, 154), radius=22, fill='#ffffff')
    draw.polygon([(56, 138), (56, 178), (89, 143)], fill='#ffffff')
    for y, end in [(70, 154), (94, 154), (118, 128)]:
        draw.rounded_rectangle((64, y, end, y + 9), radius=4, fill='#0f766e')
    image.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def copy_directory(source, target, excluded=()):
    count = 0
    for current, directories, files in os.walk(source):
        directories[:] = [name for name in directories if name not in {'__pycache__', *excluded}]
        relative = Path(current).relative_to(source)
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith(('.pyc', '.pyo')):
                continue
            shutil.copy2(Path(current) / name, destination / name)
            count += 1
            if count % 3000 == 0:
                print(f'Copied {count:,} files from {source.name}...', flush=True)
    return count


def archive_release(output):
    output = output.resolve()
    archive = Path(str(output) + '.zip')
    if archive.exists():
        raise SystemExit(f'ZIP already exists: {archive}')
    allowed = {'_internal', 'runtime', 'app', APP_NAME + '.exe', 'app.ico', 'installed-versions.txt',
               'START HERE.txt', 'THIRD_PARTY_NOTICES.txt', 'verification.json'}
    files = []
    for path in output.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        relative = path.relative_to(output)
        if relative.parts[0] not in allowed:
            raise ValueError(f'Unexpected file in release: {relative}')
        if path.is_symlink() or path.name in {'.env', 'secrets.toml', 'desktop-self-test.json'}:
            raise ValueError(f'Private or host-specific file cannot be archived: {relative}')
        files.append(path)
    if not (output / (APP_NAME + '.exe')).is_file() or not (output / 'verification.json').is_file():
        raise ValueError('A verified desktop build is required before creating the ZIP.')
    verified = json.loads((output / 'verification.json').read_text(encoding='utf-8'))
    if not verified.get('server_health') or not verified.get('server_stopped'):
        raise ValueError('The release must pass startup and shutdown checks before archiving.')
    print('Compressing the portable ZIP...', flush=True)
    temporary_archive = Path(str(archive) + '.' + uuid.uuid4().hex[:8] + '.partial')
    with zipfile.ZipFile(temporary_archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for index, path in enumerate(files, 1):
            bundle.write(path, arcname=str(Path(output.name) / path.relative_to(output)))
            if index % 3000 == 0:
                print(f'Archived {index:,}/{len(files):,} files...', flush=True)
    with zipfile.ZipFile(temporary_archive) as bundle:
        corrupt = bundle.testzip()
        if corrupt:
            raise ValueError(f'ZIP integrity verification failed for {corrupt}')
    temporary_archive.rename(archive)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(f'ZIP: {archive} ({archive.stat().st_size / 1024 ** 2:.1f} MB)', flush=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / f'Bilingual-Caption-Studio-{VERSION}-Windows-x64')
    parser.add_argument('--no-zip', action='store_true')
    parser.add_argument('--archive-existing', type=Path, help='Create a ZIP from an already verified release folder')
    args = parser.parse_args()
    if args.archive_existing:
        archive_release(args.archive_existing)
        return
    if sys.platform != 'win32' or sys.maxsize < 2 ** 32:
        raise SystemExit('Build this Windows x64 release using Windows x64 Python.')
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f'Output already exists: {output}. Choose a new --output directory.')
    if Path(str(output) + '.zip').exists() and not args.no_zip:
        raise SystemExit('ZIP already exists. Choose a new output name.')
    work = ROOT / 'build' / ('desktop-' + uuid.uuid4().hex[:8])
    work.mkdir(parents=True)
    icon = work / 'app.ico'
    make_icon(icon)
    print('Building the desktop window launcher...', flush=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
               '--name', APP_NAME, '--icon', str(icon), '--distpath', str(work / 'launcher'),
               '--workpath', str(work / 'pyinstaller'), '--specpath', str(work),
               '--collect-all', 'webview', '--collect-all', 'pythonnet', '--collect-all', 'clr_loader',
               '--hidden-import', 'clr', '--hidden-import', 'webview.platforms.edgechromium']
    for module in ['torch', 'transformers', 'streamlit', 'pandas', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3']:
        command.extend(['--exclude-module', module])
    command.append(str(ROOT / 'desktop.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    shutil.copytree(work / 'launcher' / APP_NAME, output)
    shutil.copy2(icon, output / 'app.ico')

    print('Copying a relocatable Python runtime...', flush=True)
    runtime = output / 'runtime'
    runtime.mkdir()
    base = Path(sys.base_prefix)
    for path in base.iterdir():
        if path.is_file() and (path.suffix.lower() in {'.exe', '.dll', '.zip'} or path.name.lower().startswith('license')):
            shutil.copy2(path, runtime / path.name)
    for name in ['Lib', 'DLLs']:
        if (base / name).exists():
            copy_directory(base / name, runtime / name, excluded={'site-packages'})
    # Restrict portable Python to its own standard library/packages; never depend on the build machine's venv.
    (runtime / f'python{sys.version_info.major}{sys.version_info.minor}._pth').write_text(
        '.\nLib\nDLLs\nLib/site-packages\nimport site\n', encoding='utf-8')
    package_source = next(Path(path) for path in site.getsitepackages()
                          if (Path(path) / 'streamlit').is_dir())
    print('Copying installed app libraries (includes the speech/translation engines)...', flush=True)
    copy_directory(package_source, runtime / 'Lib' / 'site-packages')
    app = output / 'app'
    app.mkdir()
    for name in ['app.py', 'captions.py', 'engine.py', 'languages.py', 'playback.py', 'dubbing.py', 'dubbing_ui.py', 'dubbing_video.py',
                 'sample_project.json', 'README.md', 'requirements.txt']:
        shutil.copy2(ROOT / name, app / name)
    (app / '.streamlit').mkdir()
    shutil.copy2(ROOT / '.streamlit' / 'config.toml', app / '.streamlit' / 'config.toml')
    packages = sorted(f'{dist.metadata["Name"]}=={dist.version}' for dist in importlib.metadata.distributions())
    (output / 'installed-versions.txt').write_text('\n'.join(packages) + '\n', encoding='utf-8')
    (output / 'START HERE.txt').write_text(
        f'{APP_NAME} - Windows x64 {VERSION}\n\n'
        '1. Extract this entire folder before launching.\n'
        f'2. Double-click {APP_NAME}.exe. Keep runtime/, app/, and _internal/ beside it.\n'
        '3. Choose a recording and translation language, upload a clip, and generate captions.\n'
        '4. Save subtitle/project files or optionally create a dubbed video.\n\n'
        'Python is included; no Python installation or account is needed.\n'
        'Microsoft Edge WebView2 Runtime is required (normally present on Windows 10/11).\n'
        'Official installer: https://developer.microsoft.com/en-us/microsoft-edge/webview2/\n\n'
        'Native speech libraries also require the latest Microsoft Visual C++ x64 Runtime.\n'
        'Official installer: https://aka.ms/vc14/vc_redist.x64.exe\n\n'
        'First model use requires internet to download pretrained models into your user cache.\n'
        'Dubbing requires online opt-in and sends translated caption text to Microsoft.\n'
        'Closing the window stops its private local server and active processing.\n'
        'Unsaved session edits are lost on close: use Save editable project to retain them.\n'
        'Logs: %LOCALAPPDATA%\\BilingualCaptionStudio\\logs\n\n'
        'This is a Windows-only, unsigned portable build. Model weights and recordings are not included.\n'
        'Source: https://github.com/DavidPu4/Bilingual-Caption-Studio\n', encoding='utf-8')
    (output / 'THIRD_PARTY_NOTICES.txt').write_text(
        'This distribution includes CPython and third-party Python packages.\n'
        'CPython terms are in runtime/LICENSE.txt. Package license/notice files and metadata\n'
        'are retained in runtime/Lib/site-packages, including *.dist-info directories.\n'
        'installed-versions.txt lists the included packages. Pretrained model weights are\n'
        'not included and are downloaded from their upstream repositories at first use.\n', encoding='utf-8')
    print('Checking the packaged server starts and stops...', flush=True)
    report = work / 'desktop-self-test.json'
    process = subprocess.run([str(output / (APP_NAME + '.exe')), '--self-test', '--report', str(report)],
                              cwd=output, timeout=120)
    if process.returncode or not report.exists() or not json.loads(report.read_text(encoding='utf-8')).get('ok'):
        raise SystemExit(f'Packaged server self-test failed. Inspect {report} and the desktop logs.')
    checked = json.loads(report.read_text(encoding='utf-8'))
    if not checked.get('server_stopped'):
        raise SystemExit('Packaged server did not stop cleanly.')
    verification = {'version': VERSION, 'platform': 'Windows x64', 'server_health': checked['ok'],
                    'process_tree_owned': checked.get('job_owned', False), 'server_stopped': checked['server_stopped']}
    (output / 'verification.json').write_text(json.dumps(verification, indent=2), encoding='utf-8')
    print(json.dumps(verification, indent=2), flush=True)
    if not args.no_zip:
        archive_release(output)
    print(f'Desktop app: {output / (APP_NAME + ".exe")}', flush=True)


if __name__ == '__main__':
    main()
