from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import TestCase

from src.modules.plugins.media import (
    ALLOWED_SPEED_FACTORS,
    TIME_RANGES_PATTERN,
    Media,
    build_atempo_filter,
    build_audio_thumbnail_command,
    build_static_image_video_command,
    build_telegram_thumbnail_command,
    format_ffmpeg_time,
    format_timestamp,
    invert_time_ranges,
    is_audio_thumbnail_image_message,
    merge_time_ranges,
    parse_time_ranges,
    parse_timestamp,
    supports_audio_thumbnail_message,
)


class MediaTimeRangeHelpersTest(TestCase):
    def test_parse_time_ranges_keeps_existing_cut_format(self) -> None:
        assert parse_time_ranges('00:00:00 00:30:00 00:45:00 01:15:00') == [
            (0, 1800),
            (2700, 4500),
        ]

    def test_parse_time_ranges_rejects_invalid_or_empty_ranges(self) -> None:
        invalid_ranges = [
            '',
            '00:00:10 00:00:10',
            '00:00:10 00:00:09',
            '00:60:00 01:00:00',
            '00:00:60 00:01:00',
        ]

        for value in invalid_ranges:
            with self.subTest(value=value):
                try:
                    parse_time_ranges(value)
                except ValueError:
                    pass
                else:
                    msg = f'{value!r} should be rejected'
                    raise AssertionError(msg)

    def test_time_range_prompt_pattern_still_accepts_multiple_pairs(self) -> None:
        assert TIME_RANGES_PATTERN.match('00:00:00 00:30:00 00:45:00 01:15:00')
        assert TIME_RANGES_PATTERN.match('/media cut 00:00:00 00:30:00') is None

    def test_format_helpers_match_ffmpeg_and_caption_usage(self) -> None:
        assert parse_timestamp('01:02:03') == 3723
        assert format_timestamp(3723) == '01:02:03'
        assert format_ffmpeg_time(3) == '3'
        assert format_ffmpeg_time(3.25) == '3.25'

    def test_merge_time_ranges_sorts_and_merges_overlaps(self) -> None:
        assert merge_time_ranges([(8, 10), (2, 4), (4, 6), (5, 7)]) == [
            (2, 7),
            (8, 10),
        ]

    def test_invert_time_ranges_returns_segments_to_keep(self) -> None:
        assert invert_time_ranges([(2, 4), (6, 8)], 10) == [
            (0.0, 2),
            (4, 6),
            (8, 10),
        ]

    def test_invert_time_ranges_handles_edges_overlap_and_out_of_bounds(self) -> None:
        assert invert_time_ranges([(0, 2), (2, 5), (12, 20)], 8) == [(5, 8)]
        assert invert_time_ranges([(0, 10)], 8) == []

    def test_static_image_video_command_uses_low_frame_rate(self) -> None:
        command = build_static_image_video_command(
            input_file=Path('image.jpg'),
            audio_file=Path('audio.ogg'),
            output_file=Path('output.mp4'),
            duration=3.25,
        )

        assert '-loop 1 -framerate 1 -i "image.jpg"' in command
        assert '-t 3.25' in command
        assert '-r 1' in command
        assert 'format=yuv420p' in command
        assert '-shortest' not in command


class MediaSpeedHelpersTest(TestCase):
    def test_speed_factor_buttons_put_slow_factors_first(self) -> None:
        assert ALLOWED_SPEED_FACTORS[:3] == [0.25, 0.5, 0.75]
        assert 1 not in ALLOWED_SPEED_FACTORS

    def test_build_atempo_filter_handles_slow_and_fast_factors(self) -> None:
        assert build_atempo_filter(0.75) == 'atempo=0.75'
        assert build_atempo_filter(0.25) == 'atempo=0.5,atempo=0.5'
        assert build_atempo_filter(3) == 'atempo=2,atempo=1.5'


class AudioThumbnailHelpersTest(TestCase):
    def make_audio_message(self, ext: str, *, voice: bool = False) -> Any:
        return SimpleNamespace(
            audio=True,
            voice=voice,
            file=SimpleNamespace(ext=ext, mime_type='audio/mpeg'),
        )

    def make_image_message(self, mime_type: str = 'image/png') -> Any:
        return SimpleNamespace(
            photo=None,
            file=SimpleNamespace(ext='.png', mime_type=mime_type),
        )

    def test_audio_thumbnail_support_is_limited_to_music_formats(self) -> None:
        assert supports_audio_thumbnail_message(self.make_audio_message('.mp3'))
        assert supports_audio_thumbnail_message(self.make_audio_message('.m4a'))
        assert supports_audio_thumbnail_message(self.make_audio_message('.m4b'))
        assert not supports_audio_thumbnail_message(self.make_audio_message('.ogg'))
        assert not supports_audio_thumbnail_message(self.make_audio_message('.mp3', voice=True))

    def test_audio_thumbnail_image_accepts_photo_or_image_file(self) -> None:
        assert is_audio_thumbnail_image_message(SimpleNamespace(photo=object(), file=None))
        assert is_audio_thumbnail_image_message(self.make_image_message())
        assert not is_audio_thumbnail_image_message(self.make_image_message('application/pdf'))

    def test_build_telegram_thumbnail_command_outputs_small_jpeg_shape(self) -> None:
        command = build_telegram_thumbnail_command(Path('cover.png'), Path('thumbnail.jpg'), 320, 8)

        assert '-vf "scale=320:320:force_original_aspect_ratio=increase,crop=320:320"' in command
        assert '-frames:v 1' in command
        assert '-q:v 8' in command
        assert command.endswith('"thumbnail.jpg"')

    def test_build_audio_thumbnail_command_uses_format_specific_cover_mapping(self) -> None:
        mp3_command = build_audio_thumbnail_command(
            Path('input.mp3'), Path('cover.jpg'), Path('output.mp3')
        )
        m4a_command = build_audio_thumbnail_command(
            Path('input.m4a'), Path('cover.jpg'), Path('output.m4a')
        )

        assert '-id3v2_version 3' in mp3_command
        assert 'comment="Cover (front)"' in mp3_command
        assert '-disposition:v attached_pic' in m4a_command
        assert '-map 0:a -map 1:v' in m4a_command


class MediaCommandPatternsTest(TestCase):
    def test_existing_media_cut_command_pattern_still_matches(self) -> None:
        match = Media.commands['media cut'].pattern.match(
            '/media cut 00:00:00 00:30:00 00:45:00 01:15:00'
        )

        assert match is not None
        assert match.group(1) == 'media'
        assert match.group(2) == 'cut'
        assert match.group(3) == '00:00:00 00:30:00 00:45:00 01:15:00'

    def test_media_crop_out_command_pattern_matches_inverse_cut_command(self) -> None:
        match = Media.commands['media crop'].pattern.match(
            '/media crop out 00:01:00 00:02:00 00:05:00 00:05:30'
        )

        assert match is not None
        assert match.group(1) == 'media'
        assert match.group(2) == 'crop'
        assert match.group(3) == '00:01:00 00:02:00 00:05:00 00:05:30'

    def test_media_crop_out_does_not_shadow_existing_media_cut_command(self) -> None:
        assert Media.commands['media crop'].pattern.match('/media cut 00:00:00 00:30:00') is None

    def test_media_speed_command_pattern_accepts_slow_factors(self) -> None:
        match = Media.commands['media speed'].pattern.match('/media speed 0.5')

        assert match is not None
        assert match.group(1) == 'media'
        assert match.group(2) == 'speed'
        assert match.group(3) == '0.5'

    def test_audio_thumbnail_command_pattern_matches(self) -> None:
        match = Media.commands['audio thumbnail'].pattern.match('/audio thumbnail')

        assert match is not None
        assert match.group(1) == 'audio'
        assert match.group(2) == 'thumbnail'
