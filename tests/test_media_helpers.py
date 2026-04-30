from unittest import TestCase

from src.modules.plugins.media import (
    TIME_RANGES_PATTERN,
    Media,
    format_ffmpeg_time,
    format_timestamp,
    invert_time_ranges,
    merge_time_ranges,
    parse_time_ranges,
    parse_timestamp,
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
