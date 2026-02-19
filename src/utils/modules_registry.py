"""Bot modules dynamic loader"""

import logging
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import cast

import orjson
from telethon.events import NewMessage

from src import STATE_DIR
from src.modules.base import ModuleBase
from src.utils.permission_manager import PermissionManager

logger = logging.getLogger(__name__)

PLUGINS_DIR = STATE_DIR / 'plugins'


def load_user_plugins() -> list[ModuleType]:
    PLUGINS_DIR.mkdir(exist_ok=True)
    if str(PLUGINS_DIR) not in sys.path:
        sys.path.append(str(PLUGINS_DIR))

    found_modules: list[ModuleType] = []
    for entry in sorted(PLUGINS_DIR.glob('*.py'), key=lambda p: p.name):
        if entry.name.startswith(('.', '_')) or entry.name == '__init__.py':
            continue
        try:
            found_modules.append(import_module(entry.stem))
        except Exception:
            logger.exception(f'Failed to load user plugin: {entry}')

    if found_modules:
        logger.info(f'found user plugins: {", ".join(sorted({m.__name__ for m in found_modules}))}')
    return found_modules


def load_modules(directory: str) -> list[ModuleBase]:
    """Load all modules in modules list"""
    built_in_modules: list[ModuleType] = [
        import_module(f'{directory}.modules.{module.parent.name}.{module.stem}')
        for module in filter(
            lambda x: (
                x.name not in ('__init__.py', 'base.py') and x.suffix == '.py' and x.is_file()
            ),
            Path(f'{directory}/modules').glob('**/*.py'),
        )
    ]
    found_modules = [*built_in_modules, *load_user_plugins()]
    logger.info(
        f'found modules: {", ".join([module.__name__.split(".")[-1] for module in found_modules])}'
    )

    loaded_module_classes: list[ModuleBase] = []
    for module in found_modules:
        for module_class in filter(
            lambda x: (
                getattr(x, 'IS_MODULE', False)
                and x.__name__ not in ('ModuleBase', 'InlineModuleBase')
            ),
            (getattr(module, i) for i in dir(module)),
        ):
            loaded_module_classes.append(module_class())
    loaded_modules_summary = ', '.join(
        [f'{module.name} {list(module.commands.keys())}' for module in loaded_module_classes]
    )
    logger.info(f'loaded modules: {loaded_modules_summary}')
    return loaded_module_classes


class ModuleRegistry:
    """
    Module registry

    This class is used to register modules and their commands.
    """

    def __init__(self, directory: str, permission_manager: PermissionManager) -> None:
        self.modules: list[ModuleBase] = load_modules(directory)
        self.permission_manager = permission_manager
        self.modules_file = STATE_DIR / 'modules.json'
        self.modules_status: dict[str, bool] = self._load_modules_status()

        for module in self.modules:
            for command, cmd in module.commands.items():
                if cmd.name is None:
                    cmd.name = command

        self.command_to_module: dict[str, ModuleBase] = {
            command: module for module in self.modules for command in module.commands
        }

    def _load_modules_status(self) -> dict[str, bool]:
        if self.modules_file.exists():
            return cast(dict[str, bool], orjson.loads(self.modules_file.read_text()))
        return {module.name: True for module in self.modules}

    def _save_modules_status(self) -> None:
        self.modules_file.write_bytes(
            orjson.dumps(self.modules_status, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        )

    def enable_module(self, module_name: str) -> None:
        self.modules_status[module_name] = True
        self._save_modules_status()

    def disable_module(self, module_name: str) -> None:
        self.modules_status[module_name] = False
        self._save_modules_status()

    def is_module_enabled(self, module_name: str) -> bool:
        return self.modules_status.get(module_name, True)

    async def get_applicable_modules(self, event: NewMessage.Event) -> list[ModuleBase]:
        user_id = event.sender_id or event.chat_id
        return [
            module
            for module in self.modules
            if self.is_module_enabled(module.name)
            and self.permission_manager.has_permission(module.name, user_id, event.chat_id)
            and (await module.is_applicable(event))
        ]

    def get_module_by_command(self, command: str) -> ModuleBase | None:
        module = self.command_to_module.get(command)
        if module and self.is_module_enabled(module.name):
            return module
        return None

    def get_all_commands(self, event: NewMessage.Event) -> dict[str, ModuleBase.CommandsT]:
        user_id = event.sender_id or event.chat_id
        return {
            module.name: module.commands
            for module in self.modules
            if self.is_module_enabled(module.name)
            and self.permission_manager.has_permission(module.name, user_id, event.chat_id)
        }

    async def get_applicable_commands(self, event: NewMessage.Event) -> list[str]:
        return [
            command
            for module in await self.get_applicable_modules(event)
            for command in module.commands
            if module.commands[command].is_applicable_for_reply
            and module.commands[command].condition(event, None)
        ]
