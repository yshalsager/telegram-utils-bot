from unittest import TestCase

from src.modules.plugins.ytdlp import (
    pick_storyboard_format,
    youtube_thumbnail_urls,
)


class YtdlpHelperTest(TestCase):
    def test_youtube_thumbnail_urls_fallback_from_maxres_to_default(self) -> None:
        assert youtube_thumbnail_urls('https://youtu.be/abc123_DEF0?si=x') == [
            'https://img.youtube.com/vi/abc123_DEF0/maxresdefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/sddefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/hqdefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/mqdefault.jpg',
            'https://img.youtube.com/vi/abc123_DEF0/default.jpg',
        ]

    def test_pick_storyboard_prefers_mid_sized_gallery_sheets(self) -> None:
        info = {
            'formats': [
                {'format_id': 'sb0', 'format_note': 'storyboard', 'fragments': [{}] * 50},
                {'format_id': 'sb1', 'format_note': 'storyboard', 'fragments': [{}] * 18},
            ]
        }

        assert pick_storyboard_format(info)['format_id'] == 'sb1'
