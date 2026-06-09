from dataclasses import dataclass, field
from html import escape
from pathlib import Path


@dataclass(frozen=True)
class HttpRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteFile:
    name: str
    url: str
    size: str = ''
    headers: dict[str, str] = field(default_factory=dict)
    cookie_file: Path | None = None
    source: str = ''

    @property
    def request(self) -> HttpRequest:
        return HttpRequest(self.url, self.headers)

    def to_html(self) -> str:
        name = self.name
        if self.size:
            name = f'{name} ({self.size})'
        return f'<a href="{escape(self.url, quote=True)}">{escape(name)}</a>'


@dataclass(frozen=True)
class ExternalDownload:
    name: str
    command: tuple[str, ...]
    output_dir: Path


DownloadPlan = list[RemoteFile] | ExternalDownload
DirectAsset = RemoteFile
