from pathlib import Path
from shutil import which
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp
import regex as re

from src import STATE_DIR

GDL_DOWNLOAD_URL = (
    'https://raw.githubusercontent.com/Akianonymus/gdrive-downloader/master/release/gdl'
)
GDL_PATH = STATE_DIR / 'bin' / 'gdl'
GDL_REQUIRED_PROGRAMS = ('bash', 'curl', 'jq', 'xargs')
GDRIVE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{10,}$')
GDRIVE_DIRECT_URL_PATTERN = re.compile(r'https?://drive\.google\.com/[^\s<>"\']+')
GDRIVE_URL_PATTERN = re.compile(
    r'https?://(?:'
    r'drive\.google\.com/[^\s<>"\']*id=[A-Za-z0-9_-]+'
    r'|drive\.google\.com/[^\s<>"\']*file/d/[A-Za-z0-9_-]+'
    r'|drive\.google\.com/[^\s<>"\']*drive[^\s<>"\']*folders/[A-Za-z0-9_-]+'
    r'|docs\.google\.com/[^\s<>"\']*/d/[A-Za-z0-9_-]+'
    r')[^\s<>"\']*'
)
GDRIVE_CONFIRM_PATTERN = re.compile(
    r'(?:confirm=([A-Za-z0-9_-]+)|name=["\']confirm["\'][^>]+value=["\']([A-Za-z0-9_-]+))'
)


def extract_gdrive_input(text: str) -> str | None:
    if match := re.search(GDRIVE_URL_PATTERN, text):
        return match.group(0).rstrip('.,،)')

    text = text.strip()
    if re.fullmatch(GDRIVE_ID_PATTERN, text):
        return text
    return None


def is_gdrive_folder_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == 'drive.google.com' and '/folders/' in parsed.path


def extract_gdrive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc != 'drive.google.com' or is_gdrive_folder_url(url):
        return None

    query_id = (parse_qs(parsed.query).get('id') or [''])[0]
    if re.fullmatch(GDRIVE_ID_PATTERN, query_id):
        return query_id

    parts = [part for part in parsed.path.strip('/').split('/') if part]
    if len(parts) >= 3 and parts[0] == 'file' and parts[1] == 'd':
        file_id = parts[2]
        if re.fullmatch(GDRIVE_ID_PATTERN, file_id):
            return file_id
    return None


def build_gdrive_direct_url(file_id: str, confirm: str = '') -> str:
    query = [('export', 'download')]
    if confirm:
        query.append(('confirm', confirm))
    query.append(('id', file_id))
    return urlunparse(('https', 'drive.google.com', '/uc', '', urlencode(query), ''))


def parse_gdrive_confirm_token(html: str) -> str:
    if match := re.search(GDRIVE_CONFIRM_PATTERN, html):
        return next(group for group in match.groups() if group)
    return ''


def collect_downloaded_files(download_dir: Path) -> list[Path]:
    return sorted(path for path in download_dir.rglob('*') if path.is_file())


def missing_gdl_dependencies() -> list[str]:
    return [program for program in GDL_REQUIRED_PROGRAMS if which(program) is None]


async def ensure_gdrive_downloader() -> Path:
    if GDL_PATH.exists():
        return GDL_PATH

    GDL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = GDL_PATH.with_suffix('.tmp')
    async with aiohttp.ClientSession() as session, session.get(GDL_DOWNLOAD_URL) as response:
        response.raise_for_status()
        temp_path.write_bytes(await response.read())
    temp_path.chmod(0o755)
    temp_path.replace(GDL_PATH)
    return GDL_PATH
