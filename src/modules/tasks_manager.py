import logging
from asyncio import Task
from datetime import UTC, datetime

from humanize import naturaltime
from telethon.events import NewMessage

from src import BOT_ADMINS
from src.bot import bot


async def list_tasks(event: NewMessage.Event) -> None:
    active_tasks: dict[str, Task] = getattr(event.client, 'active_tasks', {})
    if not active_tasks:
        await event.reply('No active tasks.')
        return

    message = '<b>Active tasks:</b>\n\n'
    for task_id, task in active_tasks.items():
        message += f'📦 <code>{task_id}</code>'
        try:
            task_event: NewMessage.Event
            if task_event := task.get_coro().cr_frame.f_locals.get('event'):
                start_time = event.date.replace(tzinfo=UTC)
                message += (
                    f' (<code>{task_event.message.text}</code>) - '
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
    task_id = event.pattern_match.group(1)
    active_tasks = getattr(event.client, 'active_tasks', {})

    if task_id == 'all':
        for task_id in list(active_tasks.keys()):
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


bot.add_event_handler(
    list_tasks,
    NewMessage(pattern=r'^/tasks$', func=lambda e: e.is_private and e.sender_id in BOT_ADMINS),
)

bot.add_event_handler(
    cancel_task,
    NewMessage(
        pattern=r'^/tasks\s+cancel\s+([\w_]+)$',
        func=lambda e: e.is_private and e.sender_id in BOT_ADMINS,
    ),
)
