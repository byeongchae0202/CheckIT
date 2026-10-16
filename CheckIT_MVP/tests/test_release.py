from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.package_release import package


class ReleaseTests(unittest.TestCase):
    def test_archive_uses_placeholder_and_excludes_runtime_data(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'release.zip'
            package(project, destination)
            with zipfile.ZipFile(destination) as archive:
                names = archive.namelist()
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read('CheckIT_MVP/local_settings.py'), (project / 'local_settings.example.py').read_bytes())
                self.assertIn('CheckIT_MVP/data/weather_regions.json', names)
                self.assertIn('CheckIT_MVP/personal.py', names)
                self.assertIn('CheckIT_MVP/camera.py', names)
                self.assertIn('CheckIT_MVP/camera_frontend/index.html', names)
                self.assertFalse(any(name.endswith(('.sqlite3', '.pt', '.pyc')) for name in names))
                self.assertFalse(any('.checkit-tools' in name or '.venv' in name or '__pycache__' in name for name in names))
