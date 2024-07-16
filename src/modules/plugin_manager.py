from telethon.events import NewMessage, StopPropagation

from src import BOT_ADMINS, bot
from src.modules.base import ModuleBase


async def list_plugins(event: NewMessage.Event) -> None:
    enabled_modules = []
    disabled_modules = []

    for module in bot.modules_registry.modules:
        enabled_modules.append(module.name) if bot.modules_registry.modules_status.get(
            module.name, False
        ) else disabled_modules.append(module.name)

    enabled_text = ', '.join(sorted(enabled_modules)) if enabled_modules else 'None'
    disabled_text = ', '.join(sorted(disabled_modules)) if disabled_modules else 'None'
    await event.reply(
        f'<b>Enabled modules</b>: {enabled_text}\n' f'<b>Disabled modules</b>: {disabled_text}',
    )
    raise StopPropagation


async def list_commands(event: NewMessage.Event) -> None:
    all_commands: dict[str, ModuleBase.CommandsT] = bot.modules_registry.get_all_commands()
    help_text = '<b>Available commands</b>:\n\n'
    for module, commands in all_commands.items():
        help_text += f'<i>{module.upper()}</i>:\n'
        for cmd, data in commands.items():
            help_text += f'/{cmd}: {data['description']}\n'
        help_text += '\n'
    await event.reply(help_text)
    raise StopPropagation


async def manage_plugins(event: NewMessage.Event) -> None:
    action, module_name = event.pattern_match.groups()
    if action == 'enable':
        bot.modules_registry.enable_module(module_name)
        await event.reply(f'Module {module_name} enabled')
    elif action == 'disable':
        bot.modules_registry.disable_module(module_name)
        await event.reply(f'Module {module_name} disabled')
    else:
        await event.reply('Invalid action. Use "enable" or "disable".')


bot.bot.add_event_handler(
    list_plugins,
    NewMessage(
        pattern='^/plugins$', func=lambda x: x.is_private and x.message.sender_id in BOT_ADMINS
    ),
)

bot.bot.add_event_handler(
    list_commands,
    NewMessage(
        pattern='/commands', func=lambda x: x.is_private and x.message.sender_id in BOT_ADMINS
    ),
)

bot.bot.add_event_handler(
    manage_plugins,
    NewMessage(
        pattern=r'^/plugins\s+(enable|disable)\s+(\w+)',
        func=lambda x: x.is_private and x.message.sender_id in BOT_ADMINS,
    ),
)
