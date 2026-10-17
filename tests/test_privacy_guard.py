import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GUARD = Path(__file__).resolve().parents[1] / "scripts/check_private.py"


class PrivacyGuardTests(unittest.TestCase):
    def test_unquoted_env_secret_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", folder], check=True)
            marker = "a1b2c3d4" * 4
            (root / ".env.example").write_text(f"AMAP_KEY={marker}\n")
            subprocess.run(["git", "add", ".env.example"], cwd=root, check=True)
            result = subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(marker, result.stderr + result.stdout)

    def test_secret_deleted_from_tip_is_still_blocked_in_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", folder], check=True)
            marker = "a1b2c3d4" * 4
            source = root / "settings.py"
            source.write_text(f'AMAP_KEY = "{marker}"\n')
            subprocess.run(["git", "add", "settings.py"], cwd=root, check=True)
            identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null"]
            subprocess.run(["git", *identity, "commit", "-qm", "initial"], cwd=root, check=True)
            subprocess.run(["git", "rm", "-q", "settings.py"], cwd=root, check=True)
            subprocess.run(["git", *identity, "commit", "-qm", "remove"], cwd=root, check=True)
            result = subprocess.run([sys.executable, str(GUARD), "--history"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("settings.py", result.stderr)
            self.assertNotIn(marker, result.stderr + result.stdout)

    def test_staged_secret_is_blocked_even_after_working_copy_is_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", folder], check=True)
            marker = "a1b2c3d4" * 4
            source = root / "settings.py"
            source.write_text(f'AMAP_KEY = "{marker}"\n')
            subprocess.run(["git", "add", "settings.py"], cwd=root, check=True)
            source.write_text('AMAP_KEY = ""\n')
            result = subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("settings.py", result.stderr)
            self.assertNotIn(marker, result.stderr + result.stdout)

    def test_private_data_filename_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", folder], check=True)
            (root / "waypoints.json").write_text('{}\n')
            subprocess.run(["git", "add", "waypoints.json"], cwd=root, check=True)
            result = subprocess.run([sys.executable, str(GUARD), "--all"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("private-file", result.stderr)

    def test_private_preferences_and_watches_are_blocked(self):
        for name in ("preferences.json", "trip.watch.json", "trip.state.watch.json", "trip.itinerary.json", ".private/note.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                subprocess.run(["git", "init", "-q", folder], check=True)
                target = root / name
                target.parent.mkdir(exist_ok=True)
                target.write_text('{}\n')
                subprocess.run(["git", "add", name], cwd=root, check=True)
                result = subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("private-file", result.stderr)

    def test_clean_source_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", folder], check=True)
            (root / "tool.py").write_text('print("hello")\n')
            subprocess.run(["git", "add", "tool.py"], cwd=root, check=True)
            result = subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
