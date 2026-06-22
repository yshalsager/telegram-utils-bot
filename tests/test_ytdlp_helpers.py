from unittest import TestCase

from src.modules.plugins.ytdlp import youtube_thumbnail_urls


class YtdlpHelperTest(TestCase):
    def test_youtube_thumbnail_urls_fallback_from_maxres_to_default(self) -> None:
        assert youtube_thumbnail_urls('https://youtu.be/abc123_DEF0?si=x') == [
            'https://img.youtube.com/vi/abc123_DEF0/maxresdefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/sddefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/hqdefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/mqdefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/default.jpg',
        ]
