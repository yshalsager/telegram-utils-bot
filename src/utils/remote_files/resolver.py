from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from re import Pattern
from typing import cast

import regex as re

from src.utils.archive_org import ARCHIVE_URL_PATTERN
from src.utils.google_drive import GDRIVE_DIRECT_URL_PATTERN
from src.utils.remote_files.models import DownloadPlan, RemoteFile
from src.utils.remote_files.providers import (
    APKCOMBO_URL_PATTERN,
    APKPURE_URL_PATTERN,
    APTOIDE_URL_PATTERN,
    DROPBOX_URL_PATTERN,
    FDROID_URL_PATTERN,
    FOURPDA_ATTACHMENT_URL_PATTERN,
    GITHUB_RELEASE_URL_PATTERN,
    HUAWEI_APPGALLERY_URL_PATTERN,
    IZZYONDROID_URL_PATTERN,
    MEDIAFIRE_URL_PATTERN,
    ONEDRIVE_URL_PATTERN,
    PIXELDRAIN_URL_PATTERN,
    SOURCEFORGE_URL_PATTERN,
    YANDEX_DISK_URL_PATTERN,
    resolve_4pda_attachment,
    resolve_apkcombo,
    resolve_apkpure,
    resolve_aptoide,
    resolve_archive,
    resolve_dropbox,
    resolve_fdroid,
    resolve_gdrive,
    resolve_github_release,
    resolve_huawei_appgallery,
    resolve_izzyondroid,
    resolve_mediafire,
    resolve_onedrive,
    resolve_pixeldrain,
    resolve_sourceforge,
    resolve_yandex_disk,
)


@dataclass(frozen=True)
class SourceProvider:
    name: str
    patterns: tuple[Pattern, ...]
    resolver: Callable[[str], Awaitable[list[RemoteFile]]]

    def matches(self, url: str) -> bool:
        return any(re.search(pattern, url) for pattern in self.patterns)

    async def resolve(self, url: str) -> DownloadPlan:
        return await self.resolver(url)


PROVIDERS = [
    # General file hosts.
    SourceProvider('mediafire', (MEDIAFIRE_URL_PATTERN,), resolve_mediafire),
    SourceProvider('sourceforge', (SOURCEFORGE_URL_PATTERN,), resolve_sourceforge),
    SourceProvider('pixeldrain', (PIXELDRAIN_URL_PATTERN,), resolve_pixeldrain),
    SourceProvider('github-release', (GITHUB_RELEASE_URL_PATTERN,), resolve_github_release),
    SourceProvider('archive-org', (ARCHIVE_URL_PATTERN,), resolve_archive),
    SourceProvider('4pda', (FOURPDA_ATTACHMENT_URL_PATTERN,), resolve_4pda_attachment),
    # Cloud storage links.
    SourceProvider('onedrive', (ONEDRIVE_URL_PATTERN,), resolve_onedrive),
    SourceProvider('yandex-disk', (YANDEX_DISK_URL_PATTERN,), resolve_yandex_disk),
    SourceProvider('dropbox', (DROPBOX_URL_PATTERN,), resolve_dropbox),
    SourceProvider('google-drive', (GDRIVE_DIRECT_URL_PATTERN,), resolve_gdrive),
    # Android app sources.
    SourceProvider('fdroid', (FDROID_URL_PATTERN,), resolve_fdroid),
    SourceProvider('izzyondroid', (IZZYONDROID_URL_PATTERN,), resolve_izzyondroid),
    SourceProvider('apkpure', (APKPURE_URL_PATTERN,), resolve_apkpure),
    SourceProvider('aptoide', (APTOIDE_URL_PATTERN,), resolve_aptoide),
    SourceProvider(
        'huawei-appgallery', (HUAWEI_APPGALLERY_URL_PATTERN,), resolve_huawei_appgallery
    ),
    SourceProvider('apkcombo', (APKCOMBO_URL_PATTERN,), resolve_apkcombo),
]


def get_matching_provider(url: str) -> SourceProvider | None:
    for provider in PROVIDERS:
        if provider.matches(url):
            return provider
    return None


def is_supported_remote_url(url: str) -> bool:
    return get_matching_provider(url) is not None


async def resolve_download_plan(url: str) -> DownloadPlan:
    if provider := get_matching_provider(url):
        return await provider.resolve(url)
    return []


async def resolve_remote_files(url: str) -> list[RemoteFile]:
    plan = await resolve_download_plan(url)
    return cast(list[RemoteFile], plan) if isinstance(plan, list) else []


resolve_direct_links = resolve_remote_files
