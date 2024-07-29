import logging
from asyncio import Task
from datetime import UTC, datetime
from typing import ClassVar

import regex as re
from humanize import naturaltime
from telethon.events import CallbackQuery, NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private
from src.utils.telegram import get_reply_message


async def list_tasks(event: NewMessage.Event) -> None:
    active_tasks: dict[str, Task] = getattr(event.client, 'active_tasks', {})
    if not active_tasks:
        await event.reply('No active tasks.')
        return
    reply_message = await get_reply_message(event) or event.message
    current_task_id = f'{reply_message.chat_id}_{reply_message.id}'

    message = '<b>Active tasks:</b>\n\n'
    for task_id, task in active_tasks.items():
        if task_id == current_task_id:
            continue
        message += f'📦 <code>{task_id}</code>'
        try:
            task_event: NewMessage.Event | CallbackQuery.Event
            if task_event := task.get_coro().cr_frame.f_locals.get('event'):
                task_command = (
                    task_event.message.text
                    if hasattr(task_event, 'message')
                    else task_event.data.decode()
                )
                start_time = event.date.replace(tzinfo=UTC)
                message += (
                    f' (<code>{task_command}</code>) - '
                    f"👤 <a href='tg://user?id={task_event.sender_id}'>{task_event.sender_id}</a> - "
                    f'🗓 <code>{start_time}</code> - ⏰ <code>{naturaltime(datetime.now(UTC) - start_time)}</code>'
                )
            else:
                message += ' - <i>Unknown</i>\n'
        except Exception as err:  # noqa: BLE001
            logging.error(err)
            message += f"Couldn't get command info {err!s}\n"

            # message += f"Status: {'Running' if not task.done() else 'Completed'}\n"
        # message += f"Cancelled: {'Yes' if task.cancelled() else 'No'}\n"
        # if task.done():
        #     if task.exception():
        #         message += f'Exception: {task.exception()[:-300]}\n'
        #     elif task.cancelled():
        #         message += 'Task was cancelled\n'
        #     else:
        #         message += f'Result: {task.result()}\n'

        message += '\n'
    await event.reply(message)


async def cancel_task(event: NewMessage.Event) -> None:
    """Cancel a specific task."""
    message = await get_reply_message(event) or event.message
    current_task_id = f'{message.chat_id}_{message.id}'
    task_id = event.message.text.split('cancel ')[1]
    active_tasks = getattr(event.client, 'active_tasks', {})

    if task_id == 'all':
        for task_id in list(active_tasks.keys()):
            if task_id == current_task_id:
                continue
            task = active_tasks[task_id]
            task.cancel()
            del active_tasks[task_id]
        await event.reply('All tasks have been cancelled.')
        return

    if task_id not in active_tasks:
        await event.reply(f'Task with ID {task_id} not found.')
        return

    task = active_tasks[task_id]
    if task.done():
        await event.reply(f'Task {task_id} has already completed.')
    else:
        task.cancel()
        await event.reply(f'Task {task_id} has been cancelled.')

    del active_tasks[task_id]


class TasksManager(ModuleBase):
    name = 'Tasks Manager'
    description = 'Manage tasks running in the bot'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'tasks': Command(
            handler=list_tasks,
            description='List all active tasks',
            pattern=re.compile(r'^/tasks$'),
            condition=is_admin_in_private,
        ),
        'tasks cancel': Command(
            handler=cancel_task,
            description='Cancel a specific task or all tasks',
            pattern=re.compile(r'^/tasks\s+cancel\s+([\w_]+)$'),
            condition=is_admin_in_private,
        ),
    }
