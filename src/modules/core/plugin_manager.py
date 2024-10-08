from typing import ClassVar

import regex as re
from telethon.events import NewMessage
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopePeer

from src import bot
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private
from src.utils.i18n import t


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
        f'<b>{t("enabled_modules")}</b>: {enabled_text}\n'
        f'<b>{t("disabled_modules")}</b>: {disabled_text}',
    )


async def list_commands(event: NewMessage.Event) -> None:
    all_commands: dict[str, ModuleBase.CommandsT] = bot.modules_registry.get_all_commands(event)
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
    action, module_name = event.message.text.split('plugins ')[1].split(' ')
    if action == 'enable':
        bot.modules_registry.enable_module(module_name)
        await event.reply(t('module_enabled', module_name=module_name))
    if action == 'disable':
        bot.modules_registry.disable_module(module_name)
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
            pattern=re.compile(r'^/commands$'),
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


bot.bot.add_event_handler(
    list_commands,
    NewMessage(func=lambda x: x.message.text in ('/commands', '/help')),
)
