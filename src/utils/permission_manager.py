from pathlib import Path

import orjson


class PermissionManager:
    def __init__(self, bot_admins: set[int], permissions_file: Path = Path('permissions.json')):
        self.bot_admins = bot_admins
        self.permissions_file = Path(permissions_file)
        self.module_permissions: dict[str, set[int]] = self._load_permissions()

    def _load_permissions(self) -> dict[str, set[int]]:
        if self.permissions_file.exists():
            permissions = orjson.loads(self.permissions_file.read_text())
            return {module: set(users) for module, users in permissions.items()}
        return {}

    def _save_permissions(self) -> None:
        permissions = {module: list(users) for module, users in self.module_permissions.items()}
        self.permissions_file.write_bytes(
            orjson.dumps(permissions, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        )

    def add_user_to_module(self, module_name: str, user_id: int) -> None:
        if module_name not in self.module_permissions:
            self.module_permissions[module_name] = set()
        self.module_permissions[module_name].add(user_id)
        self._save_permissions()

    def remove_user_from_module(self, module_name: str, user_id: int) -> None:
        if module_name in self.module_permissions:
            self.module_permissions[module_name].discard(user_id)
            self._save_permissions()

    def has_permission(self, module_name: str, user_id: int) -> bool:
        if user_id in self.bot_admins:
            return True
        return user_id in self.module_permissions.get(module_name, set())
