from asyncio import Task, current_task
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import ClassVar

import regex as re
from humanize import naturaltime
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private
from src.utils.i18n import t


@dataclass
class ActiveTask:
    task: Task[bool]
    command: str
    user_id: int
    started_at: datetime


def next_task_id(active_task_ids: Collection[str], base_task_id: str) -> str:
    task_id = base_task_id
    suffix = 2
    while task_id in active_task_ids:
        task_id = f'{base_task_id}_{suffix}'
        suffix += 1
    return task_id


async def list_tasks(event: NewMessage.Event) -> None:
    active_tasks: dict[str, ActiveTask] = getattr(event.client, 'active_tasks', {})
    tasks = [
        (task_id, active)
        for task_id, active in active_tasks.items()
        if active.task is not current_task()
    ]
    if not tasks:
        await event.reply(t('no_active_tasks'))
        return

    now = datetime.now(UTC)
    lines = [
        f'📦 <code>{task_id}</code> (<code>{escape(active.command)}</code>) - '
        f"👤 <a href='tg://user?id={active.user_id}'>{active.user_id}</a> - "
        f'🗓 <code>{active.started_at}</code> - '
        f'⏰ <code>{naturaltime(now - active.started_at)}</code>'
        for task_id, active in tasks
    ]
    await event.reply(f'<b>{t("active_tasks")}:</b>\n\n' + '\n'.join(lines))


async def cancel_task(event: NewMessage.Event) -> None:
    """Cancel a specific task."""
    task_id = event.message.text.split('cancel ')[1]
    active_tasks: dict[str, ActiveTask] = getattr(event.client, 'active_tasks', {})

    if task_id == 'all':
        for task_id, active in list(active_tasks.items()):
            if active.task is current_task():
                continue
            active.task.cancel()
            active_tasks.pop(task_id, None)
        await event.reply(t('all_tasks_cancelled'))
        return

    active = active_tasks.get(task_id)
    if not active:
        await event.reply(t('task_not_found', task_id=task_id))
        return

    if active.task.done():
        await event.reply(t('task_already_completed', task_id=task_id))
    else:
        active.task.cancel()
        await event.reply(t('task_cancelled', task_id=task_id))

    active_tasks.pop(task_id, None)


class TasksManager(ModuleBase):
    name = 'Tasks Manager'
    description = t('_tasks_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'tasks': Command(
            handler=list_tasks,
            description=t('_tasks_description'),
            pattern=re.compile(r'^/tasks$'),
            condition=is_admin_in_private,
        ),
        'tasks cancel': Command(
            handler=cancel_task,
            description=t('_tasks_cancel_description'),
            pattern=re.compile(r'^/tasks\s+cancel\s+([\w_]+)$'),
            condition=is_admin_in_private,
        ),
    }
