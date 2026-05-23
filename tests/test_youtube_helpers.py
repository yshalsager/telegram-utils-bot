from datetime import UTC, datetime
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import orjson
from src.modules.plugins.youtube import (
    build_youtube_resource,
    get_youtube_client_config,
    parse_credentials_expiry,
    parse_youtube_upload_args,
)


class YouTubeHelpersTest(TestCase):
    def test_parse_youtube_upload_args_defaults_to_private(self) -> None:
        assert parse_youtube_upload_args('Title | Description | tag one, tag two') == {
            'privacy_status': 'private',
            'title': 'Title',
            'description': 'Description',
            'tags': ['tag one', 'tag two'],
        }

    def test_parse_youtube_upload_args_accepts_privacy_prefix(self) -> None:
        assert parse_youtube_upload_args('unlisted | Title') == {
            'privacy_status': 'unlisted',
            'title': 'Title',
            'description': '',
            'tags': [],
        }

    def test_build_youtube_resource_sets_required_upload_metadata(self) -> None:
        assert build_youtube_resource(
            title='Title',
            description='Description',
            tags=['tag'],
            privacy_status='private',
        ) == {
            'snippet': {
                'title': 'Title',
                'description': 'Description',
                'tags': ['tag'],
                'categoryId': '22',
            },
            'status': {
                'privacyStatus': 'private',
                'selfDeclaredMadeForKids': False,
            },
        }

    def test_get_youtube_client_config_prefers_direct_env(self) -> None:
        with patch.dict(
            environ,
            {'YOUTUBE_CLIENT_ID': 'client-id', 'YOUTUBE_CLIENT_SECRET': 'client-secret'},
        ):
            assert get_youtube_client_config() == ('client-id', 'client-secret')

    def test_get_youtube_client_config_reads_client_secrets_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            secrets_file = Path(temp_dir) / 'client_secret.json'
            secrets_file.write_bytes(
                orjson.dumps(
                    {'installed': {'client_id': 'file-client-id', 'client_secret': 'file-secret'}}
                )
            )
            with patch.dict(
                environ,
                {
                    'YOUTUBE_CLIENT_SECRETS_FILE': str(secrets_file),
                    'YOUTUBE_CLIENT_ID': '',
                    'YOUTUBE_CLIENT_SECRET': '',
                },
            ):
                assert get_youtube_client_config() == ('file-client-id', 'file-secret')

    def test_parse_credentials_expiry_returns_naive_utc_datetime(self) -> None:
        assert parse_credentials_expiry('2026-05-23T10:15:30Z') == datetime(
            2026, 5, 23, 10, 15, 30, tzinfo=UTC
        ).replace(tzinfo=None)
