from datetime import UTC, datetime
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import orjson
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from src.modules.plugins.youtube import (
    YOUTUBE_PATTERN,
    YOUTUBE_TOKEN_URL,
    build_youtube_resource,
    generate_channel_alias,
    get_youtube_client_config,
    get_youtube_partner_config,
    load_youtube_credentials,
    normalize_alias,
    parse_credentials_expiry,
    parse_youtube_upload_args,
    save_youtube_credentials,
    youtube_auth_path,
    youtube_pending_auth_path,
    youtube_token_path,
)
from src.utils.i18n import t


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

    def test_youtube_pattern_accepts_upload_panel_command(self) -> None:
        match = YOUTUBE_PATTERN.match('/youtube upload public | Title')

        assert match
        assert match.group(1) == 'upload'
        assert match.group(2) == 'public | Title'

    def test_youtube_panel_translation_does_not_trigger_plural_formatting(self) -> None:
        assert '2' in t('youtube_panel', channel_count=2)

    def test_youtube_user_state_paths_are_scoped_by_user_and_alias(self) -> None:
        assert normalize_alias(' Main_Channel ') == 'main_channel'
        assert (
            youtube_token_path(123, 'Main')
            .as_posix()
            .endswith('state/youtube/users/123/tokens/main.json')
        )
        assert (
            youtube_auth_path(123, 'Main')
            .as_posix()
            .endswith('state/youtube/users/123/auth/main.json')
        )
        assert (
            youtube_pending_auth_path(123)
            .as_posix()
            .endswith('state/youtube/users/123/auth/pending.json')
        )

    def test_generate_channel_alias_uses_channel_title_and_deduplicates(self) -> None:
        channels = {'my-channel': {'channel_id': 'UC1'}}

        assert generate_channel_alias(channels, 'My Channel', 'UC2') == 'my-channel-2'
        assert generate_channel_alias(channels, 'Other', 'UC1') == 'my-channel'

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

    def test_get_youtube_partner_config_requires_content_owner_and_channel(self) -> None:
        with patch.dict(
            environ,
            {
                'YOUTUBE_CONTENT_OWNER_ID': 'owner-id',
                'YOUTUBE_CONTENT_OWNER_CHANNEL_ID': 'channel-id',
            },
        ):
            assert get_youtube_partner_config() == {
                'onBehalfOfContentOwner': 'owner-id',
                'onBehalfOfContentOwnerChannel': 'channel-id',
            }

    def test_parse_credentials_expiry_returns_naive_utc_datetime(self) -> None:
        assert parse_credentials_expiry('2026-05-23T10:15:30Z') == datetime(
            2026, 5, 23, 10, 15, 30, tzinfo=UTC
        ).replace(tzinfo=None)

    def test_save_youtube_credentials_encrypts_token_file(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            patch.dict(
                environ,
                {
                    'STATE_ENCRYPTION_KEY': Fernet.generate_key().decode(),
                    'YOUTUBE_CLIENT_ID': 'client-id',
                    'YOUTUBE_CLIENT_SECRET': 'client-secret',
                },
            ),
            patch('src.modules.plugins.youtube.YOUTUBE_USERS_DIR', Path(temp_dir)),
        ):
            access_value = 'access-value'
            refresh_value = 'refresh-value'
            client_value = 'client-value'
            token_path = youtube_token_path(123, 'main')
            credentials = Credentials(
                token=access_value,
                refresh_token=refresh_value,
                token_uri=YOUTUBE_TOKEN_URL,
                client_id='client-id',
                client_secret=client_value,
                scopes=['scope'],
            )

            save_youtube_credentials(credentials, token_path)

            encrypted = token_path.read_text()
            assert access_value not in encrypted
            loaded_credentials = load_youtube_credentials(123, 'main')
            assert loaded_credentials is not None
            assert loaded_credentials.token == access_value

    def test_load_youtube_credentials_removes_revoked_refresh_token(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            patch.dict(
                environ,
                {
                    'STATE_ENCRYPTION_KEY': Fernet.generate_key().decode(),
                    'YOUTUBE_CLIENT_ID': 'client-id',
                    'YOUTUBE_CLIENT_SECRET': 'client-secret',
                },
            ),
            patch('src.modules.plugins.youtube.YOUTUBE_USERS_DIR', Path(temp_dir)),
            patch.object(Credentials, 'refresh', side_effect=RefreshError('invalid_grant')),
        ):
            token_path = youtube_token_path(123, 'main')
            access_value = 'access-value'
            refresh_value = 'refresh-value'
            client_value = 'client-secret'
            credentials = Credentials(
                token=access_value,
                refresh_token=refresh_value,
                token_uri=YOUTUBE_TOKEN_URL,
                client_id='client-id',
                client_secret=client_value,
                scopes=['scope'],
                expiry=datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None),
            )
            save_youtube_credentials(credentials, token_path)

            assert load_youtube_credentials(123, 'main') is None
            assert not token_path.exists()
