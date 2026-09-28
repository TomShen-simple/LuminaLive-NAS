import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "disable_unverified_backups.py"


class BackupSafetyPatchTest(unittest.TestCase):
    def test_check_apply_and_repeat(self):
        source = (
            "import os\nMAX_BACKUP_LISTS = 3\n"
            "def blocked_urls():\n    return set()\n"
            "def render_backup_lists(main, state, nas_state=None):\n"
            "    lines = main.splitlines()\n    header = lines[0]\n"
            "    playlists = [[header] for _ in range(MAX_BACKUP_LISTS)]\n"
            "    counts = [0] * MAX_BACKUP_LISTS\n"
            "    blocked = blocked_urls()\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publish-nas-playlist.py"
            path.write_text(source, encoding="utf-8")
            check = subprocess.run([sys.executable, str(SCRIPT), "check", "--path", str(path)],
                                   capture_output=True, text=True, check=True)
            self.assertIn("patch applicable", check.stdout)
            self.assertEqual(source, path.read_text(encoding="utf-8"))
            subprocess.run([sys.executable, str(SCRIPT), "apply", "--path", str(path)],
                           capture_output=True, text=True, check=True)
            updated = path.read_text(encoding="utf-8")
            self.assertIn("LUMINA_ALLOW_UNVERIFIED_BACKUPS", updated)
            self.assertEqual(3, len(__import__("runpy").run_path(str(path))["render_backup_lists"](
                "#EXTM3U\n#EXTINF:-1,CCTV5\nhttps://bad.example/live.m3u8\n", {})))
            self.assertTrue(all(count == 0 for _, count in
                                __import__("runpy").run_path(str(path))["render_backup_lists"](
                                    "#EXTM3U\n#EXTINF:-1,CCTV5\nhttps://bad.example/live.m3u8\n", {})))
            subprocess.run([sys.executable, str(SCRIPT), "apply", "--path", str(path)],
                           capture_output=True, text=True, check=True)
            self.assertEqual(updated, path.read_text(encoding="utf-8"))
