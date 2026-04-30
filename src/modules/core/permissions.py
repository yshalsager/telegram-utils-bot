from typing import ClassVar

import regex as re
from telethon import TelegramClient
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private
from src.utils.i18n import t
from src.utils.telegram import reply_in_chunks


async def manage_permissions(event: NewMessage.Event) -> None:
    permission_manager = event.client.permission_manager
    match = re.match(r'^/permissions\s+(add|remove)\s+([\w, ]+)\s+(-?\d+)$', event.message.text)
    if not match:
        await event.reply(t('permissions_invalid_command'))
        return
    action, _modules, user_id = match.groups()
    try:
        user_id = int(user_id)
    except ValueError:
        await event.reply(t('invalid_user_id'))
        return

    results = []
    for module_name in [module.strip() for module in _modules.split(',')]:
        if action == 'add':
            permission_manager.add_user_to_module(module_name, user_id)
            results.append(t('permissions_user_added', user_id=user_id, module_name=module_name))
        elif action == 'remove':
            permission_manager.remove_user_from_module(module_name, user_id)
            results.append(t('permissions_user_removed', user_id=user_id, module_name=module_name))

    await reply_in_chunks(event, '', results)


async def list_permissions(event: NewMessage.Event) -> None:
    permission_manager = event.client.permission_manager
    if not permission_manager.module_permissions:
        await event.reply(t('no_permissions_found'))
        return

    header = f'<b>{t("modules_permissions")}</b>:'
    lines = []
    for module, users in permission_manager.module_permissions.items():
        lines.append(f'<i>{module}</i>')
        lines.extend(f'- <a href="tg://user?id={user}">{user}</a>' for user in users)
        lines.append('')
    await reply_in_chunks(event, header, lines)


async def user_permissions(event: NewMessage.Event) -> None:
    permission_manager = event.client.permission_manager
    match = re.match(r'^/permissions\s+(\d+)$', event.message.text)
    if not match:
        await event.reply(t('invalid_user_id'))
        return
    user_id = int(match.group(1))

    user_modules = [
        module
        for module, users in permission_manager.module_permissions.items()
        if user_id in users
    ]

    if not user_modules:
        await event.reply(t('user_has_no_permissions', user_id=user_id))
        return

    await reply_in_chunks(
        event,
        f'<a href="tg://user?id={user_id}">{user_id}</a> <b>has access to:</b>',
        [f'- {module}' for module in user_modules],
    )


async def list_all_users(event: NewMessage.Event) -> None:
    permission_manager = event.client.permission_manager
    user_to_modules: dict[int, list[str]] = {}
    for module, users in permission_manager.module_permissions.items():
        for user_id in users:
            if user_id not in user_to_modules:
                user_to_modules[user_id] = []
            user_to_modules[user_id].append(module)

    if not user_to_modules:
        await event.reply(t('no_users_found'))
        return

    header = f'<b>{t("all_users_with_permissions")}:</b>'
    lines = []
    for user_id, modules in sorted(user_to_modules.items()):
        lines.append(f'<a href="tg://user?id={user_id}">{user_id}</a>')
        lines.extend(f'- {module}' for module in modules)
        lines.append('')

    await reply_in_chunks(event, header, lines)


class Permissions(ModuleBase):
    name = 'Permissions'
    description = t('_permissions_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'permissions add': Command(
            handler=manage_permissions,
            description=t('_permissions_add_description'),
            pattern=re.compile(r'^/permissions\s+add\s+([\w, ]+)\s+(-?\d+)$'),
            condition=is_admin_in_private,
        ),
        'permissions remove': Command(
            handler=manage_permissions,
            description=t('_permissions_remove_description'),
            pattern=re.compile(r'^/permissions\s+remove\s+([\w, ]+)\s+(-?\d+)$'),
            condition=is_admin_in_private,
        ),
        'permissions': Command(
            handler=list_permissions,
            description=t('_permissions_description'),
            pattern=re.compile(r'^/permissions$'),
            condition=is_admin_in_private,
        ),
        'users': Command(
            handler=list_all_users,
            description=t('_users_description'),
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
