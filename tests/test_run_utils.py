import asyncio
import sys
import time
from shlex import quote

from src.utils.run import run_command, run_subprocess_shell


def test_streamed_subprocess_timeout_is_total_runtime() -> None:
    async def run() -> list[tuple[str, int | None]]:
        code = 'import time\nfor i in range(20):\n print(i, flush=True); time.sleep(0.05)'
        command = f'{quote(sys.executable)} -c {quote(code)}'
        return [item async for item in run_subprocess_shell(command, timeout=0.2)]

    started = time.monotonic()
    output = asyncio.run(run())

    assert time.monotonic() - started < 0.8
    assert output[-1][1] is None
    assert '19' not in output[-1][0]


def test_run_command_kills_process_after_timeout() -> None:
    async def run() -> tuple[str, int]:
        return await run_command(
            f'{quote(sys.executable)} -c "import time; time.sleep(10)"', timeout=0.1
        )

    output, code = asyncio.run(run())

    assert code == -1
    assert output
