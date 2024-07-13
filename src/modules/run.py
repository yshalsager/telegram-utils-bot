from contextlib import suppress
from datetime import UTC, datetime
from tempfile import NamedTemporaryFile

from telethon.errors import MessageNotModifiedError
from telethon.events import NewMessage

from src import BOT_ADMINS
from src.modules.base import ModuleBase
from src.utils.run import MAX_MESSAGE_LENGTH, run_subprocess


async def stream_shell_output(event: NewMessage.Event, cmd: str) -> None:
    status_message = await event.reply('Starting process...')
    progress_message = await event.reply('<pre>Process output:</pre>')
    buffer = ''
    code = None
    async for full_log, return_code in run_subprocess(cmd):
        buffer, code = full_log, return_code
        if bool(buffer.strip()):
            with suppress(MessageNotModifiedError):
                await progress_message.edit(
                    f'<pre>{buffer if len(buffer) < MAX_MESSAGE_LENGTH else buffer[:MAX_MESSAGE_LENGTH]}</pre>'
                )

    status = 'Process completed' if code == 0 else f'Process failed with return code {code}'
    start_time = event.date.replace(tzinfo=UTC)
    end_time = datetime.now(UTC)
    elapsed_time = end_time - start_time
    status += (
        f'\nStarted at {event.date.strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'Finished at {datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'Elapsed time: {elapsed_time}'
    )
    await status_message.edit(status)
    with NamedTemporaryFile(
        mode='w+', prefix=f'{event.date.strftime("%Y%m%d_%H%M%S")}_', suffix='.log'
    ) as temp_file:
        temp_file.write(buffer)
        temp_file.seek(0)  # Go back to the start of the file to ensure it's read from the beginning
        await event.client.send_file(
            event.chat_id,
            file=temp_file.name,
        )


class SubprocessModule(ModuleBase):
    @property
    def name(self) -> str:
        return 'Subprocess'

    @property
    def description(self) -> str:
        return 'Run a shell command and stream its output.'

    def commands(self) -> ModuleBase.CommandsT:
        return {'shell': {'handler': self.run_command, 'description': 'Run a shell command'}}

    def is_applicable(self, event: NewMessage.Event) -> bool:
        # only bot owner can run shell commands
        return bool(event.message.text.startswith('/shell') and event.sender_id == BOT_ADMINS[0])

    @staticmethod
    async def run_command(event: NewMessage.Event) -> None:
        cmd = event.message.text.split(maxsplit=1)[1] if len(event.message.text.split()) > 1 else ''
        if not cmd:
            await event.reply('Please provide a command to run.')
            return

        await stream_shell_output(event, cmd)
