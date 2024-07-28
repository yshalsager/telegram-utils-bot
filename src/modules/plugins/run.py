from asyncio import sleep
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from tempfile import NamedTemporaryFile
from typing import ClassVar

import regex as re
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.events import NewMessage
from telethon.tl.custom import Message

from src import BOT_ADMINS
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_owner_in_private
from src.utils.run import (
    ADMIN_TIMEOUT_SECONDS,
    MAX_MESSAGE_LENGTH,
    TIMEOUT_SECONDS,
    run_subprocess_exec,
    run_subprocess_shell,
)
from src.utils.telegram import delete_message_after

SECONDS_TO_WAIT = 3


async def stream_shell_output(
    event: NewMessage.Event,
    cmd: str,
    status_message: Message | None = None,
    progress_message: Message | None = None,
    shell: bool = True,
    max_length: int = MAX_MESSAGE_LENGTH,
) -> str:
    if not status_message:
        status_message = await event.reply('Starting process...')
    if not progress_message:
        progress_message = await event.reply('<pre>Process output:</pre>')
    runner = run_subprocess_shell if shell else run_subprocess_exec
    timeout = TIMEOUT_SECONDS if event.sender_id not in BOT_ADMINS else ADMIN_TIMEOUT_SECONDS
    buffer = ''
    code = None
    last_edit_time = datetime.now()
    edit_interval = timedelta(seconds=SECONDS_TO_WAIT)

    async for full_log, return_code in runner(cmd, timeout=timeout):
        buffer, code = full_log, return_code
        if bool(buffer.strip()):
            current_time = datetime.now()
            if current_time - last_edit_time >= edit_interval:
                try:
                    await progress_message.edit(
                        f'<pre>{buffer if len(buffer) < max_length else buffer[:max_length]}</pre>'
                    )
                    last_edit_time = current_time
                    edit_interval = timedelta(seconds=SECONDS_TO_WAIT)
                except MessageNotModifiedError:
                    pass
                except FloodWaitError as e:
                    edit_interval = timedelta(seconds=e.seconds) + timedelta(
                        seconds=SECONDS_TO_WAIT
                    )
            else:
                await sleep(0.1)

    # Final update
    if not buffer:
        buffer = 'Empty output'
    with suppress(MessageNotModifiedError):
        await progress_message.edit(
            f'<pre>{buffer if len(buffer) < MAX_MESSAGE_LENGTH else buffer[:MAX_MESSAGE_LENGTH]}</pre>'
        )

    status = 'Process completed' if code == 0 else f'Process failed with return code {code}'
    start_time = (
        event.date.replace(tzinfo=UTC)
        if hasattr(event, 'date')
        else status_message.date.replace(tzinfo=UTC)
    )
    end_time = datetime.now(UTC)
    elapsed_time = end_time - start_time
    status += (
        f'\nStarted at {start_time.strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'Finished at {datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'Elapsed time: {elapsed_time}'
    )
    await status_message.edit(status)
    event.client.loop.create_task(delete_message_after(progress_message))

    if bool(buffer.strip()) and event.sender_id in BOT_ADMINS:
        with NamedTemporaryFile(
            mode='w+', prefix=f'{start_time.strftime("%Y%m%d_%H%M%S")}_', suffix='.log'
        ) as temp_file:
            temp_file.write(buffer)
            temp_file.seek(
                0
            )  # Go back to the start of the file to ensure it's read from the beginning
            await event.client.send_file(
                event.chat_id,
                file=temp_file.name,
            )
    return status


async def run_shell(event: NewMessage.Event) -> None:
    await stream_shell_output(event, event.message.text.replace('/shell ', '', 1), shell=True)  # noqa: S604


async def run_exec(event: NewMessage.Event) -> None:
    await stream_shell_output(event, event.message.text.replace('/exec ', '', 1), shell=False)


class Shell(ModuleBase):
    name = 'Subprocess'
    description = 'Run a shell command and stream its output.'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'shell': Command(
            handler=run_shell,
            description='Run a shell command',
            pattern=re.compile(r'^/shell\s+(.+)$'),
            condition=is_owner_in_private,
        ),
        'exec': Command(
            handler=run_exec,
            description='Execute a command',
            pattern=re.compile(r'^/exec\s+(.+)$'),
            condition=is_owner_in_private,
        ),
    }
