from typing import ClassVar

import regex as re
from telethon import TelegramClient
from telethon.events import NewMessage

from src.bot import permission_manager
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private


async def manage_permissions(event: NewMessage.Event) -> None:
    action, _modules, user_id = re.match(
        r'^/permissions\s+(add|remove)\s+([\w, ]+)\s+(\d+)$', event.message.text
    ).groups()
    try:
        user_id = int(user_id)
    except ValueError:
        await event.reply('Invalid user ID. Please provide a valid integer.')
        return

    results = []
    for module_name in [module.strip() for module in _modules.split(',')]:
        if action == 'add':
            permission_manager.add_user_to_module(module_name, user_id)
            results.append(f'User {user_id} added to module {module_name}')
        elif action == 'remove':
            permission_manager.remove_user_from_module(module_name, user_id)
            results.append(f'User {user_id} removed from module {module_name}')

    await event.reply('\n'.join(results))


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


class Permissions(ModuleBase):
    name = 'Permissions'
    description = 'Manage user permissions for different modules'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'permissions add': Command(
            handler=manage_permissions,
            description='Add user permissions for one or more modules',
            pattern=re.compile(r'^/permissions\s+add\s+([\w, ]+)\s+(\d+)$'),
            condition=is_admin_in_private,
        ),
        'permissions remove': Command(
            handler=manage_permissions,
            description='Remove user permissions for one or more modules',
            pattern=re.compile(r'^/permissions\s+remove\s+([\w, ]+)\s+(\d+)$'),
            condition=is_admin_in_private,
        ),
        'permissions': Command(
            handler=list_permissions,
            description='List all module permissions',
            pattern=re.compile(r'^/permissions$'),
            condition=is_admin_in_private,
        ),
        'users': Command(
            handler=list_all_users,
            description='List all users with their permissions',
            pattern=re.compile(r'^/users$'),
            condition=is_admin_in_private,
        ),
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        bot.add_event_handler(
            user_permissions,
            NewMessage(
                pattern=r'^/permissions\s+(\d+)$',
                func=lambda e: is_admin_in_private(e, e.message),
            ),
        )
