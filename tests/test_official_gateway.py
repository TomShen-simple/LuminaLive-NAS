import unittest

from app.official_gateway import rewrite_playlist


class RewriteTest(unittest.TestCase):
    def test_removes_nas_credentials(self):
        result = rewrite_playlist('#EXTM3U\n#EXTINF:-1,CCTV1\n/api/migu/608807420/index.m3u8?access_token=PRIVATE\n',
                                  'https://example.test/live-official/PLAYKEY')
        self.assertNotIn('PRIVATE', result)
        self.assertIn('https://example.test/live-official/PLAYKEY/api/migu/608807420/index.m3u8', result)

    def test_preserves_signed_asset(self):
        result = rewrite_playlist('#EXTM3U\n#EXT-X-MAP:URI="/api/migu/asset/abc.def"\n/api/migu/asset/xyz.sig\n', '/prefix')
        self.assertIn('URI="/prefix/api/migu/asset/abc.def"', result)
        self.assertIn('/prefix/api/migu/asset/xyz.sig', result)

    def test_rejects_external_and_arbitrary_urls(self):
        for value in ['https://other.test/x', '//other.test/x', '/api/admin', '/api/migu/asset/../secret']:
            with self.assertRaises(ValueError):
                rewrite_playlist('#EXTM3U\n' + value, '/prefix')

    def test_rejects_non_playlist(self):
        with self.assertRaises(ValueError):
            rewrite_playlist('<html>oops</html>', '/prefix')
