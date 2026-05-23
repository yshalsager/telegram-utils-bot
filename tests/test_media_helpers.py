from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import TestCase

from src.modules.plugins.media import (
    ALLOWED_SPEED_FACTORS,
    TIME_RANGES_PATTERN,
    Media,
    build_amplify_command,
    build_atempo_filter,
    build_audio_compress_command,
    build_audio_thumbnail_command,
    build_convert_media_command,
    build_convert_to_audio_command,
    build_crop_out_filter_command,
    build_cut_media_command,
    build_filter_concat_command,
    build_fix_stereo_command,
    build_mute_video_command,
    build_resize_video_command,
    build_set_metadata_command,
    build_speed_audio_command,
    build_speed_video_command,
    build_split_media_command,
    build_static_image_video_command,
    build_telegram_thumbnail_command,
    build_video_audio_update_command,
    build_video_compress_command,
    build_video_thumbnail_grid_command,
    build_voice_note_command,
    build_x265_command,
    calculate_video_compress_bitrate,
    can_copy_audio_to_suffix,
    concat_file_line,
    copy_concat_signature,
    double_quoted_shell_value,
    ffmpeg_filter_value,
    format_bitrate_arg,
    format_ffmpeg_time,
    format_timestamp,
    invert_time_ranges,
    is_audio_thumbnail_image_message,
    merge_time_ranges,
    parse_time_ranges,
    parse_timestamp,
    supports_audio_thumbnail_message,
    video_thumbnail_timestamps,
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

        assert '-loop 1 -framerate 1 -i image.jpg' in command
        assert '-t 3.25' in command
        assert '-r 1' in command
        assert 'format=yuv420p' in command
        assert '-shortest' not in command

    def test_crop_out_filter_command_uses_precise_trim_concat(self) -> None:
        command = build_crop_out_filter_command(
            Path('input.mp4'),
            Path('output.mp4'),
            [(0.0, 2), (4, 6)],
            has_video_stream=True,
            has_audio_stream=True,
        )

        assert 'trim=start=0:end=2,setpts=PTS-STARTPTS' in command
        assert 'atrim=start=4:end=6,asetpts=PTS-STARTPTS' in command
        assert 'concat=n=2:v=1:a=1[v][a]' in command
        assert '-map "[v]" -map "[a]"' in command
        assert '-c copy' not in command

    def test_cut_command_keeps_only_audio_and_video_streams(self) -> None:
        command = build_cut_media_command(Path('input.mkv'), Path('output.mkv'), 2, 4.5)

        assert '-ss 2 -to 4.5' in command
        assert '-map 0:v? -map 0:a? -dn -sn' in command
        assert '-map 0 ' not in command
        assert '-c copy' in command

    def test_filter_concat_command_reencodes_compatible_stream_shapes(self) -> None:
        command = build_filter_concat_command(
            [Path('input-1.mp4'), Path('input-2.mp4')],
            Path('output.mp4'),
            has_video_stream=True,
            has_audio_stream=True,
            target_width=1280,
            target_height=720,
        )

        assert '-i input-1.mp4 -i input-2.mp4' in command
        assert 'scale=1280:720:force_original_aspect_ratio=decrease' in command
        assert 'concat=n=2:v=1:a=1[v][a]' in command
        assert '-c:v libx264' in command
        assert '-c:a aac' in command

    def test_copy_concat_signature_includes_stream_shape_and_params(self) -> None:
        base_info = {
            'vcodec': 'h264',
            'acodec': 'aac',
            'width': 1280,
            'height': 720,
            'pix_fmt': 'yuv420p',
            'avg_frame_rate': '30/1',
            'sample_rate': '48000',
            'channels': 2,
            'channel_layout': 'stereo',
            'attached_pic': False,
        }
        changed_info = {**base_info, 'width': 1920}

        assert copy_concat_signature(Path('a.mp4'), base_info) == copy_concat_signature(
            Path('b.mp4'), base_info
        )
        assert copy_concat_signature(Path('a.mp4'), base_info) != copy_concat_signature(
            Path('b.mp4'), changed_info
        )

    def test_format_bitrate_arg_uses_default_for_missing_probe_value(self) -> None:
        assert format_bitrate_arg(128000, '96k') == '128000'
        assert format_bitrate_arg(0, '96k') == '96k'

    def test_shell_escaping_helpers_quote_untrusted_values(self) -> None:
        line = concat_file_line(Path("dir/clip's name.mp4"))
        assert line.startswith("file '")
        assert "'\"'\"'" in line
        assert line.endswith("s name.mp4'\n")
        assert double_quoted_shell_value('x"$`y') == 'x\\"\\$\\`y'
        assert ffmpeg_filter_value("a:b'c") == "a\\:b\\'c"

    def test_audio_copy_compatibility_is_container_aware(self) -> None:
        assert can_copy_audio_to_suffix('.mp4', 'aac')
        assert not can_copy_audio_to_suffix('.mp4', 'opus')
        assert can_copy_audio_to_suffix('.mkv', 'opus')

    def test_video_audio_update_copies_audio_when_container_allows_it(self) -> None:
        copy_command = build_video_audio_update_command(
            Path('video.mp4'), Path('audio.m4a'), Path('out.mp4'), 'aac'
        )
        encode_command = build_video_audio_update_command(
            Path('video.mp4'), Path('audio.ogg'), Path('out.mp4'), 'opus'
        )

        assert '-map 0:v:0 -map 1:a:0' in copy_command
        assert '-c:v copy -c:a copy' in copy_command
        assert '-c:v copy -c:a aac -b:a 96k' in encode_command
        assert '-movflags +faststart' in copy_command

    def test_resize_video_command_uses_crf_and_explicit_streams(self) -> None:
        command = build_resize_video_command(480)

        assert 'scale=width=-2:height=480:force_original_aspect_ratio=decrease' in command
        assert '-map 0:v:0 -map 0:a? -dn -sn' in command
        assert '-crf 23 -preset veryfast' in command
        assert '-b:v' not in command
        assert '-c:a copy' in command

    def test_split_media_command_keeps_only_audio_and_video_streams(self) -> None:
        command = build_split_media_command(Path('input.mkv'), Path('segment_%03d.mkv'), 60)

        assert '-f segment -segment_time 60' in command
        assert '-map 0:v? -map 0:a? -dn -sn' in command
        assert '-c copy' in command

    def test_speed_video_command_handles_silent_videos(self) -> None:
        audio_command = build_speed_video_command(
            Path('input.mp4'),
            Path('output.mp4'),
            2,
            'atempo=2',
            has_audio_stream=True,
        )
        silent_command = build_speed_video_command(
            Path('input.mp4'),
            Path('output.mp4'),
            2,
            'atempo=2',
            has_audio_stream=False,
        )

        assert '[0:a:0]atempo=2[a]' in audio_command
        assert '-map "[v]" -map "[a]"' in audio_command
        assert '[0:a' not in silent_command
        assert '-an' in silent_command

    def test_video_compress_bitrate_is_clamped(self) -> None:
        assert calculate_video_compress_bitrate(1, 10_000, 90) == '128k'
        assert calculate_video_compress_bitrate(10_000_000, 10, 50) == '4M'

        command = build_video_compress_command('128k')

        assert '-map 0:v:0 -map 0:a? -dn -sn' in command
        assert '-b:v 128k -bufsize 128k' in command
        assert '-c:a copy' in command

    def test_x265_command_uses_explicit_streams(self) -> None:
        copy_command = build_x265_command(24, 'aac')
        encode_command = build_x265_command(24, 'opus')

        assert '-map 0:v:0 -map 0:a? -dn -sn' in copy_command
        assert '-c:v libx265 -crf 24' in copy_command
        assert '-c:a copy' in copy_command
        assert '-c:a aac -b:a 96k' in encode_command
        assert '-movflags +faststart' in copy_command

    def test_process_media_template_builders_keep_expected_codecs(self) -> None:
        assert '-c:a libopus -b:a 48k' in build_voice_note_command()
        assert '-c:a aac -b:a 64k' in build_audio_compress_command('64')
        assert '-c:a copy' in build_convert_to_audio_command(copy_audio=True)
        assert '-c:a aac -b:a {audio_bitrate}' in build_convert_to_audio_command(copy_audio=False)
        assert '-metadata title="a\\"b"' in build_set_metadata_command('a"b', 'artist')
        assert '-map 0:v:0 -dn -sn -c:v copy -an' in build_mute_video_command()
        video_convert_command = build_convert_media_command(
            target_is_audio=False, output_suffix='.mp4', input_audio_codec='aac'
        )
        assert '-map 0:v:0 -map 0:a? -dn -sn' in video_convert_command
        assert '-c:a copy' in video_convert_command
        assert '-filter:a "volume=2"' in build_amplify_command(2, has_video_stream=True)
        assert '-c:v copy' in build_amplify_command(2, has_video_stream=True)
        assert '-c:a libmp3lame -q:a 2' in build_speed_audio_command('atempo=2', is_voice=False)
        assert '-c:a libopus -b:a 48k' in build_speed_audio_command('atempo=2', is_voice=True)
        assert 'pan=mono|c0=FR' in build_fix_stereo_command('FR')


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
        assert command.endswith('thumbnail.jpg')

    def test_video_thumbnail_grid_uses_timestamp_selection(self) -> None:
        assert video_thumbnail_timestamps(16, count=4) == [2, 6, 10, 14]
        assert video_thumbnail_timestamps(0) == []

        command = build_video_thumbnail_grid_command(Path('input.mp4'), Path('grid.jpg'), 16)

        assert "select='gte(t,0.5)+gte(t,1.5)" in command
        assert 'eq(n,' not in command
        assert 'tile=4x4' in command

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
