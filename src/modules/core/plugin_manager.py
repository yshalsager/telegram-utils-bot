from typing import ClassVar

import regex as re
from telethon.events import NewMessage
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopePeer

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private
from src.utils.i18n import t


async def list_plugins(event: NewMessage.Event) -> None:
    modules_registry = event.client.modules_registry
    enabled_modules = []
    disabled_modules = []

    for module in modules_registry.modules:
        enabled_modules.append(module.name) if modules_registry.modules_status.get(
            module.name, True
        ) else disabled_modules.append(module.name)

    enabled_text = ', '.join(sorted(enabled_modules)) if enabled_modules else 'None'
    disabled_text = ', '.join(sorted(disabled_modules)) if disabled_modules else 'None'
    await event.reply(
        f'<b>{t("enabled_modules")}</b>: {enabled_text}\n'
        f'<b>{t("disabled_modules")}</b>: {disabled_text}',
    )


async def list_commands(event: NewMessage.Event) -> None:
    modules_registry = event.client.modules_registry
    all_commands: dict[str, ModuleBase.CommandsT] = modules_registry.get_all_commands(event)
    help_text = f'<b>{t("available_commands")}</b>:\n\n'
    for module, commands in all_commands.items():
        if not commands:
            continue
        help_text += f'<i>{module}</i>:\n'
        for cmd, data in commands.items():
            help_text += f'/{cmd}: {data.description}\n'
        help_text += '\n'
    await event.reply(help_text)
    # Set bot commands
    await event.client(
        SetBotCommandsRequest(
            scope=BotCommandScopePeer(event.input_chat),
            lang_code='',
            commands=[
                BotCommand(command_name, command_data.description)
                for module_commands in all_commands.values()
                for command_name, command_data in module_commands.items()
                if ' ' not in command_name
            ],
        )
    )


async def manage_plugins(event: NewMessage.Event) -> None:
    modules_registry = event.client.modules_registry
    action, module_name = event.message.text.split('plugins ')[1].split(' ')
    if action == 'enable':
        modules_registry.enable_module(module_name)
        await event.reply(t('module_enabled', module_name=module_name))
    if action == 'disable':
        modules_registry.disable_module(module_name)
        await event.reply(t('module_disabled', module_name=module_name))


class PluginManager(ModuleBase):
    name = 'Plugin Manager'
    description = t('_plugins_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'plugins': Command(
            handler=list_plugins,
            description=t('_plugins_description'),
            pattern=re.compile(r'^/plugins$'),
            condition=is_admin_in_private,
        ),
        'commands': Command(
            handler=list_commands,
            description=t('_commands_description'),
            pattern=re.compile(r'^/(commands|help)$'),
            condition=is_admin_in_private,
        ),
        'help': Command(
            handler=list_commands,
            description=t('_commands_description'),
            pattern=re.compile(r'^/(commands|help)$'),
            condition=is_admin_in_private,
        ),
        'plugins enable': Command(
            handler=manage_plugins,
            description=t('_plugins_enable_description'),
            pattern=re.compile(r'^/plugins\s+enable\s+(\w+)$'),
            condition=is_admin_in_private,
        ),
        'plugins disable': Command(
            handler=manage_plugins,
            description=t('_plugins_disable_description'),
            pattern=re.compile(r'^/plugins\s+disable\s+(\w+)$'),
            condition=is_admin_in_private,
        ),
    }
