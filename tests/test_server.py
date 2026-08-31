import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import server


class ServerStateTest(unittest.TestCase):
    def test_playlist_ready_requires_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yangshi.m3u"
            with mock.patch.object(server.settings, "PLAYLIST_PATH", path):
                path.write_text("#EXTM3U\n", encoding="utf-8")
                self.assertFalse(server.playlist_ready())
                path.write_text("#EXTM3U\n#EXTINF:-1,Test\nhttps://example.test/live.m3u8\n", encoding="utf-8")
                self.assertTrue(server.playlist_ready())


if __name__ == "__main__":
    unittest.main()
