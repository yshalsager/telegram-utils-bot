from unittest import TestCase

from src.modules.core import permissions


class PermissionsCommandTest(TestCase):
    def test_user_permissions_regex_matches_existing_command(self) -> None:
        assert permissions.re.match(r'^/permissions\s+(\d+)$', '/permissions 123')
