from unittest import TestCase
from unittest.mock import patch

from src.modules.plugins.gplay import GPLAY_COMMAND_PATTERN, extract_gplay_command_input
from src.utils.gplay import (
    arch_label,
    cookie_header,
    extract_gplay_package,
    get_dispenser_url,
    normalize_arch,
)


class GPlayInputTest(TestCase):
    def test_extract_gplay_package_accepts_package_names(self) -> None:
        assert extract_gplay_package('org.mozilla.firefox') == 'org.mozilla.firefox'
        assert extract_gplay_package('com.google.android.youtube') == 'com.google.android.youtube'

    def test_extract_gplay_package_accepts_play_store_urls(self) -> None:
        assert (
            extract_gplay_package(
                'https://play.google.com/store/apps/details?id=org.mozilla.firefox&hl=en'
            )
            == 'org.mozilla.firefox'
        )
        assert (
            extract_gplay_package(
                'Try https://www.play.google.com/store/apps/details?id=com.google.android.youtube.'
            )
            == 'com.google.android.youtube'
        )

    def test_extract_gplay_package_accepts_market_urls(self) -> None:
        assert (
            extract_gplay_package('market://details?id=org.mozilla.firefox')
            == 'org.mozilla.firefox'
        )

    def test_extract_gplay_package_accepts_gplay_commands(self) -> None:
        assert extract_gplay_package('/gplay org.mozilla.firefox') == 'org.mozilla.firefox'
        assert (
            extract_gplay_package(
                '/gplay https://play.google.com/store/apps/details?id=org.mozilla.firefox'
            )
            == 'org.mozilla.firefox'
        )

    def test_extract_gplay_package_rejects_invalid_input(self) -> None:
        assert (
            extract_gplay_package('https://example.com/store/apps/details?id=org.mozilla.firefox')
            == ''
        )
        assert extract_gplay_package('not a package') == ''
        assert extract_gplay_package('1bad.package') == ''

    def test_gplay_command_pattern_accepts_reply_flow(self) -> None:
        assert GPLAY_COMMAND_PATTERN.match('/gplay')
        assert GPLAY_COMMAND_PATTERN.match('/gplay org.mozilla.firefox')
        assert extract_gplay_command_input('/gplay') == ''
        assert extract_gplay_command_input('/gplay org.mozilla.firefox') == 'org.mozilla.firefox'


class GPlayHelpersTest(TestCase):
    def test_normalize_arch_accepts_supported_aliases(self) -> None:
        assert normalize_arch('arm64') == 'arm64-v8a'
        assert normalize_arch('arm64-v8a') == 'arm64-v8a'
        assert normalize_arch('armv7') == 'armeabi-v7a'
        assert normalize_arch('armeabi-v7a') == 'armeabi-v7a'

    def test_arch_label_returns_display_names(self) -> None:
        assert arch_label('arm64') == 'ARM64'
        assert arch_label('armv7') == 'ARMv7'

    def test_cookie_header_formats_cookie_values(self) -> None:
        assert cookie_header({'MarketDA': 'abc', 'Token': 'xyz'}) == {
            'Cookie': 'MarketDA=abc; Token=xyz'
        }
        assert cookie_header({}) == {}

    def test_dispenser_url_is_opt_in(self) -> None:
        with patch.dict('os.environ', {}, clear=True):
            assert get_dispenser_url() == ''

    def test_dispenser_url_is_read_from_environment(self) -> None:
        with patch.dict('os.environ', {'GPLAY_DISPENSER_URL': ' https://example.com/api/auth/ '}):
            assert get_dispenser_url() == 'https://example.com/api/auth'
