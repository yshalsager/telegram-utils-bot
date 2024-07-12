"""Bot modules dynamic loader"""

import logging
from importlib import import_module
from pathlib import Path
from types import ModuleType

from telethon.events import NewMessage

from modules.base import ModuleBase

logger = logging.getLogger(__name__)


def load_modules(directory: str) -> list[ModuleBase]:
    """Load all modules in modules list"""
    found_modules: list[ModuleType] = [
        import_module(f'{directory}.modules.{module.stem}')
        for module in filter(
            lambda x: x.name not in ('__init__.py', 'base.py')
            and x.suffix == '.py'
            and x.is_file(),
            Path(f'{directory}/modules').glob('*.py'),
        )
    ]
    logger.info(
        f'found modules: {", ".join([module.__name__.split('.')[-1] for module in found_modules])}'
    )

    loaded_module_classes: list[ModuleBase] = []
    for module in found_modules:
        for module_class in filter(
            lambda x: getattr(x, 'IS_MODULE', False) and x.__name__ != 'ModuleBase',
            (getattr(module, i) for i in dir(module)),
        ):
            loaded_module_classes.append(module_class())
    logger.info(
        f'loaded modules: {", ".join(
            [f'{module.name} {list(module.commands().keys())}' for module in loaded_module_classes]
        )}'
    )
    return loaded_module_classes


class ModuleRegistry:
    """
    Module registry

    This class is used to register modules and their commands.
    """

    def __init__(self, directory: str) -> None:
        self.modules: list[ModuleBase] = load_modules(directory)

    def get_applicable_modules(self, event: NewMessage.Event) -> list[ModuleBase]:
        return [module for module in self.modules if module.is_applicable(event)]

    def get_module_by_command(self, command: str) -> ModuleBase | None:
        for module in self.modules:
            if command in module.commands():
                return module
        return None

    def get_all_commands(self) -> dict[str, ModuleBase.CommandsT]:
        return {module.name: module.commands() for module in self.modules}
