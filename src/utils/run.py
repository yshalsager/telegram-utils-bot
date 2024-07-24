import asyncio
import logging
from asyncio.subprocess import PIPE, Process
from collections.abc import AsyncGenerator
from os import getpgid, killpg, setsid
from shlex import split as shlex_split
from signal import SIGKILL
from typing import Any

MAX_MESSAGE_LENGTH = 4000  # Max is 4096 but we leave some buffer for formatting
# TIMEOUT_SECONDS = 60 * 10  # 10 minutes timeout for user commands
ADMIN_TIMEOUT_SECONDS = 60 * 60 * 6  # 6 hours timeout for admin commands

logger = logging.getLogger(__name__)


async def read_stream(stream: asyncio.StreamReader | None) -> AsyncGenerator[str, None]:
    if stream is None:
        return
    while True:
        _line = await stream.readline()
        if not _line:
            break
        yield f'{_line.decode().strip()}\n'


async def run_subprocess(cmd: str, **kwargs: Any) -> AsyncGenerator[tuple[str, int | None], None]:  # noqa: C901, PLR0912
    process: Process = await asyncio.create_subprocess_shell(  # noqa: S604
        cmd, stdout=PIPE, stderr=PIPE, shell=True, preexec_fn=setsid, **kwargs
    )

    output = ''
    return_code = None
    process_task = asyncio.create_task(process.wait(), name='process')
    stdout_reader = read_stream(process.stdout)
    stderr_reader = read_stream(process.stderr)

    pending: dict[str, asyncio.Task] = {
        'stdout': asyncio.create_task(stdout_reader.__anext__(), name='stdout'),  # type: ignore[arg-type]
        'stderr': asyncio.create_task(stderr_reader.__anext__(), name='stderr'),  # type: ignore[arg-type]
    }
    try:
        while pending or not process_task.done():
            done, _ = await asyncio.wait(
                [*list(pending.values()), process_task],
                timeout=ADMIN_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                if task.get_name() in ('stdout', 'stderr'):
                    try:
                        line = task.result()
                        output += line
                        yield output, None
                        pending[task.get_name()] = asyncio.create_task(
                            (  # type: ignore[arg-type]
                                stdout_reader if task.get_name() == 'stdout' else stderr_reader
                            ).__anext__(),
                            name=task.get_name(),
                        )
                    except StopAsyncIteration:
                        pending.pop(task.get_name(), None)
                elif task.get_name() == 'process':
                    return_code = task.result()

            if not done:  # Timeout occurred
                raise TimeoutError

    except StopAsyncIteration:
        pass

    except TimeoutError:
        logger.info(f'Timeout while running command: {cmd}')
        output += f'\nProcess timed out after {ADMIN_TIMEOUT_SECONDS} seconds.\n'
        yield output, None

    except Exception as err:  # noqa: BLE001
        logger.error(f'Error while running command: {cmd}')
        output += f'\nError occurred: {err}\n'
        yield output, None

    finally:
        for task in [*list(pending.values()), process_task]:
            if not task.done():
                task.cancel()
        if process.returncode is None:
            try:
                pgid = getpgid(process.pid)
                killpg(pgid, SIGKILL)
            except ProcessLookupError:
                pass  # Process already terminated

    if return_code is not None:
        yield output, return_code


async def run_command(
    command: str, timeout: int = ADMIN_TIMEOUT_SECONDS, **kwargs: Any
) -> tuple[str, int]:
    args = shlex_split(command)
    process = await asyncio.create_subprocess_exec(*args, **kwargs, stdout=PIPE, stderr=PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        return 'Process timed out', -1
    output = (stdout + stderr).decode('utf-8').strip()
    return output, (process.returncode or 0)
