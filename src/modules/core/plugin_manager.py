from typing import ClassVar

import regex as re
from telethon.events import NewMessage

from src import bot
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private


async def list_plugins(event: NewMessage.Event) -> None:
    enabled_modules = []
    disabled_modules = []

    for module in bot.modules_registry.modules:
        enabled_modules.append(module.name) if bot.modules_registry.modules_status.get(
            module.name, True
        ) else disabled_modules.append(module.name)

    enabled_text = ', '.join(sorted(enabled_modules)) if enabled_modules else 'None'
    disabled_text = ', '.join(sorted(disabled_modules)) if disabled_modules else 'None'
    await event.reply(
        f'<b>Enabled modules</b>: {enabled_text}\n' f'<b>Disabled modules</b>: {disabled_text}',
    )


async def list_commands(event: NewMessage.Event) -> None:
    all_commands: dict[str, ModuleBase.CommandsT] = bot.modules_registry.get_all_commands()
    help_text = '<b>Available commands</b>:\n\n'
    for module, commands in all_commands.items():
        if not commands:
            continue
        help_text += f'<i>{module}</i>:\n'
        for cmd, data in commands.items():
            help_text += f'/{cmd}: {data.description}\n'
        help_text += '\n'
    await event.reply(help_text)


async def manage_plugins(event: NewMessage.Event) -> None:
    action, module_name = event.message.text.split('plugins ')[1].split(' ')
    if action == 'enable':
        bot.modules_registry.enable_module(module_name)
        await event.reply(f'Module {module_name} enabled')
    if action == 'disable':
        bot.modules_registry.disable_module(module_name)
        await event.reply(f'Module {module_name} disabled')


class PluginManager(ModuleBase):
    name = 'Plugin Manager'
    description = 'Manage bot plugins and commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'plugins': Command(
            handler=list_plugins,
            description='List all plugins and their status',
            pattern=re.compile(r'^/plugins$'),
            condition=is_admin_in_private,
        ),
        'commands': Command(
            handler=list_commands,
            description='List all available commands',
            pattern=re.compile(r'^/commands$'),
            condition=is_admin_in_private,
        ),
        'plugins enable': Command(
            handler=manage_plugins,
            description='Enable a plugin',
            pattern=re.compile(r'^/plugins\s+enable\s+(\w+)$'),
            condition=is_admin_in_private,
        ),
        'plugins disable': Command(
            handler=manage_plugins,
            description='Disable a plugin',
            pattern=re.compile(r'^/plugins\s+disable\s+(\w+)$'),
            condition=is_admin_in_private,
        ),
    }
