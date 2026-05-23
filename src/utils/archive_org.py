from dataclasses import dataclass
from urllib.parse import quote as url_quote
from urllib.parse import unquote, urlparse

import aiohttp
import regex as re

ARCHIVE_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
ARCHIVE_URL_PATTERN = re.compile(
    r'https?://(?:www\.)?archive\.org/(?:details|download)/[^\s<>"\']+'
)
ARCHIVE_IGNORED_SUFFIXES = (
    '_archive.torrent',
    '_files.xml',
    '_meta.sqlite',
    '_meta.xml',
)


@dataclass(frozen=True)
class ArchiveInput:
    identifier: str
    selected_path: str = ''


@dataclass(frozen=True)
class ArchiveFile:
    name: str
    source: str = ''

    @classmethod
    def from_payload(cls, payload: dict) -> ArchiveFile:
        return cls(name=str(payload.get('name') or ''), source=str(payload.get('source') or ''))

    @property
    def is_original(self) -> bool:
        return self.source == 'original'

    @property
    def is_metadata(self) -> bool:
        return self.name.startswith('__ia_') or self.name.endswith(ARCHIVE_IGNORED_SUFFIXES)

    def download_url(self, identifier: str) -> str:
        return (
            f'https://archive.org/download/{url_quote(identifier)}/{url_quote(self.name, safe="/")}'
        )


def extract_archive_input(text: str) -> ArchiveInput | None:
    if match := re.search(ARCHIVE_URL_PATTERN, text):
        url = match.group(0).rstrip('.,،)')
        parsed = urlparse(url)
        parts = [unquote(part) for part in parsed.path.strip('/').split('/')]
        if len(parts) >= 2 and re.fullmatch(ARCHIVE_IDENTIFIER_PATTERN, parts[1]):
            return ArchiveInput(parts[1], '/'.join(parts[2:]).strip('/'))

    text = text.strip()
    if re.fullmatch(ARCHIVE_IDENTIFIER_PATTERN, text):
        return ArchiveInput(text)
    return None


def select_archive_files(files: list[ArchiveFile], selected_path: str = '') -> list[ArchiveFile]:
    selected_path = selected_path.strip('/')
    if not selected_path:
        return sorted(
            [file for file in files if file.is_original and not file.is_metadata],
            key=lambda file: file.name,
        )

    exact_matches = [file for file in files if file.name == selected_path]
    if exact_matches:
        return exact_matches

    path_matches = [file for file in files if file.name.startswith(selected_path)]
    original_matches = [file for file in path_matches if file.is_original and not file.is_metadata]
    return sorted(original_matches or path_matches, key=lambda file: file.name)


async def fetch_archive_files(identifier: str) -> list[ArchiveFile]:
    async with (
        aiohttp.ClientSession() as session,
        session.get(
            f'https://archive.org/metadata/{identifier}', params={'extended_err': '1'}
        ) as response,
    ):
        response.raise_for_status()
        payload = await response.json()
    return [ArchiveFile.from_payload(file) for file in payload.get('files', []) if file.get('name')]
