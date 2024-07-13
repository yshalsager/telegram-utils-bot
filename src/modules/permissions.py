from telethon.events import NewMessage

from src import BOT_ADMINS
from src.bot import bot, permission_manager


async def manage_permissions(event: NewMessage.Event) -> None:
    action, module_name, user_id = event.pattern_match.groups()
    try:
        user_id = int(user_id)
    except ValueError:
        await event.reply('Invalid user ID. Please provide a valid integer.')
        return

    if action == 'add':
        permission_manager.add_user_to_module(module_name, user_id)
        await event.reply(f'User {user_id} added to module {module_name}')
    elif action == 'remove':
        permission_manager.remove_user_from_module(module_name, user_id)
        await event.reply(f'User {user_id} removed from module {module_name}')
    else:
        await event.reply('Invalid action. Use "add" or "remove".')


async def list_permissions(event: NewMessage.Event) -> None:
    if not permission_manager.module_permissions:
        await event.reply('No permissions found.')
        return

    message = '<b>Module permissions</b>:\n\n'
    for module, users in permission_manager.module_permissions.items():
        message += f'<i>{module}</i>\n{", ".join(f'<a href="tg://user?id={user}">{user}</a>' for user in users)}\n'
    await event.reply(message)


async def user_permissions(event: NewMessage.Event) -> None:
    try:
        user_id = int(event.pattern_match.group(1))
    except (ValueError, TypeError):
        await event.reply('Invalid user ID. Please provide a valid integer.')
        return

    user_modules = [
        module
        for module, users in permission_manager.module_permissions.items()
        if user_id in users
    ]

    if not user_modules:
        await event.reply(f'User {user_id} has no permissions.')
        return

    message = f'<a href="tg://user?id={user_id}">{user_id}</a> <b>has access to:</b>\n\n'
    message += '\n'.join(f'- {module}' for module in user_modules)
    await event.reply(message)


async def list_all_users(event: NewMessage.Event) -> None:
    user_to_modules: dict[int, list[str]] = {}
    for module, users in permission_manager.module_permissions.items():
        for user_id in users:
            if user_id not in user_to_modules:
                user_to_modules[user_id] = []
            user_to_modules[user_id].append(module)

    if not user_to_modules:
        await event.reply('No users found.')
        return

    message = '<b>All users with permissions:</b>\n\n'
    for user_id, modules in sorted(user_to_modules.items()):
        modules_list = ', '.join(modules)
        message += f'- <a href="tg://user?id={user_id}">{user_id}</a>: {modules_list}\n'

    await event.reply(message)


bot.add_event_handler(
    manage_permissions,
    NewMessage(
        pattern=r'^/permissions\s+(add|remove)\s+(\w+)\s+(\d+)',
        func=lambda x: x.is_private and x.sender_id in BOT_ADMINS,
    ),
)

bot.add_event_handler(
    user_permissions,
    NewMessage(
        pattern=r'^/permissions\s+(\d+)$', func=lambda x: x.is_private and x.sender_id in BOT_ADMINS
    ),
)

bot.add_event_handler(
    list_permissions,
    NewMessage(pattern='^/permissions$', func=lambda x: x.is_private and x.sender_id in BOT_ADMINS),
)

bot.add_event_handler(
    list_all_users,
    NewMessage(pattern='^/users$', func=lambda x: x.is_private and x.sender_id in BOT_ADMINS),
)
