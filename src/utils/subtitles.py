from asyncio import sleep
from collections import OrderedDict
from pathlib import Path

import regex as re

from src.utils.run import run_subprocess_shell


def srt_to_txt(srt_file: Path, txt_file: Path | None = None) -> Path:
    """
    Convert an SRT subtitle file to a plain text file.

    This function reads an SRT file, removes timing information and subtitle numbers,
    eliminates duplicate lines, and writes the resulting text to a new file.

    :param srt_file: Path to the input SRT file
    :param txt_file: Path to the output TXT file (optional)
    :return: Path to the created TXT file
    """
    text_lines = OrderedDict.fromkeys(
        line.strip()
        for line in srt_file.read_text('utf-8').splitlines()
        if line.strip() and not re.match(r'^\d+$', line) and '-->' not in line
    )
    if not txt_file:
        txt_file = Path(srt_file).with_suffix('.txt')
    txt_file.write_text('\n'.join(text_lines.keys()))
    return txt_file


async def convert_subtitles(input_file: Path, srt_file: Path, txt_file: Path) -> None:
    """
    Convert VTT subtitle file to SRT and TXT formats.

    :param input_file: Path to the input VTT file
    :param srt_file: Path to the output SRT file
    :param txt_file: Path to the output TXT file
    """
    async for _output, _code in run_subprocess_shell(f'ffmpeg -i "{input_file}" "{srt_file}"'):
        await sleep(0.1)
        continue
    srt_to_txt(srt_file, txt_file)
