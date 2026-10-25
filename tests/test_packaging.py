import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.build_desktop import APP_NAME, archive_release


class PackagingTests(unittest.TestCase):
    def make_release(self, folder):
        output = Path(folder) / 'Desktop App 1.0.0'
        output.mkdir()
        (output / (APP_NAME + '.exe')).write_bytes(b'mock-launcher')
        (output / 'verification.json').write_text(json.dumps({'server_health': True, 'server_stopped': True}))
        (output / 'app').mkdir()
        (output / 'app' / 'app.py').write_text('print("test app")')
        return output

    def test_archive_preserves_folder_and_has_valid_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            output = self.make_release(folder)
            (output / 'app' / '__pycache__').mkdir()
            (output / 'app' / '__pycache__' / 'cached.pyc').write_bytes(b'cached')
            archive = archive_release(output)
            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                self.assertIn(output.name + '/' + APP_NAME + '.exe', bundle.namelist())
                self.assertFalse(any('__pycache__' in name for name in bundle.namelist()))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertTrue(archive.with_suffix('.zip.sha256').read_text().startswith(digest))
            with self.assertRaises(SystemExit):
                archive_release(output)

    def test_personal_files_and_diagnostics_are_rejected(self):
        for relative in ['local-results/private.mp4', 'app/.env', 'app/.streamlit/secrets.toml', 'desktop-self-test.json']:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                output = self.make_release(folder)
                private = output / relative
                private.parent.mkdir(parents=True, exist_ok=True)
                private.write_text('private test value')
                with self.assertRaises(ValueError):
                    archive_release(output)
                self.assertFalse(Path(str(output) + '.zip').exists())

    def test_failed_self_test_cannot_be_archived(self):
        with tempfile.TemporaryDirectory() as folder:
            output = self.make_release(folder)
            (output / 'verification.json').write_text(json.dumps({'server_health': False, 'server_stopped': True}))
            with self.assertRaises(ValueError):
                archive_release(output)
