from base64 import urlsafe_b64encode
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.parse import quote as url_quote

import aiohttp
import regex as re

from src.utils.archive_org import (
    extract_archive_input,
    fetch_archive_files,
    select_archive_files,
)
from src.utils.google_drive import (
    build_gdrive_direct_url,
    extract_gdrive_file_id,
    parse_gdrive_confirm_token,
)
from src.utils.remote_files.models import RemoteFile

DirectLink = RemoteFile
SOURCEFORGE_URL_PATTERN = re.compile(r'https?://(?:www\.)?sourceforge\.net/[^\s<>"\']+')
MEDIAFIRE_URL_PATTERN = re.compile(r'https?://(?:www\.)?mediafire\.com/[^\s<>"\']+')
PIXELDRAIN_URL_PATTERN = re.compile(r'https?://(?:www\.)?pixeldrain\.com/[^\s<>"\']+')
GITHUB_RELEASE_URL_PATTERN = re.compile(
    r'https?://github\.com/[^\s<>"\']+/releases/download/[^\s<>"\']+'
)
ONEDRIVE_URL_PATTERN = re.compile(
    r'https?://(?:1drv\.ms|onedrive\.live\.com|[\w-]+-my\.sharepoint\.com)/[^\s<>"\']+'
)
YANDEX_DISK_URL_PATTERN = re.compile(r'https?://(?:disk\.yandex\.[^\s<>"\']+|yadi\.sk)/[^\s<>"\']+')
DROPBOX_URL_PATTERN = re.compile(r'https?://(?:www\.)?dropbox\.com/[^\s<>"\']+')
FDROID_URL_PATTERN = re.compile(r'https?://(?:www\.)?f-droid\.org/[^\s<>"\']+')
IZZYONDROID_URL_PATTERN = re.compile(r'https?://apt\.izzysoft\.de/fdroid/[^\s<>"\']+')
APKPURE_URL_PATTERN = re.compile(
    r'https?://(?:m\.|www\.)?(?:apkpure\.com|apkpure\.net)/[^\s<>"\']+'
)
APKPURE_APP_ID_PATTERN = re.compile(r'^[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+$')
APKPURE_API_URL = 'https://tapi.pureapk.com/v3/get_app_his_version'
APKPURE_HEADERS = {
    'User-Agent': 'APKPure/3.20.90 (Android 15; arm64-v8a)',
    'Ual-Access-Businessid': 'projecta',
    'Ual-Access-ProjectA': '{"device_info":{"os_ver":"35"}}',
}
APTOIDE_URL_PATTERN = re.compile(r'https?://(?:[\w-]+\.){2,}aptoide\.com/[^\s<>"\']+')
APTOIDE_APP_ID_PATTERN = re.compile(r'"app":\{"id":([0-9]+)')
HUAWEI_APPGALLERY_URL_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:appgallery\.huawei\.com|appgallery\.cloud\.huawei\.com)/[^\s<>"\']+'
)
HUAWEI_APPGALLERY_ID_PATTERN = re.compile(r'^C[0-9]+$')
HUAWEI_APPGALLERY_DOWNLOAD_URL = 'https://appgallery.cloud.huawei.com/appdl'
APKCOMBO_URL_PATTERN = re.compile(r'https?://(?:www\.)?apkcombo\.com/[^\s<>"\']+')
APKCOMBO_HEADERS = {
    'User-Agent': 'curl/8.0.1',
    'Accept': '*/*',
    'Connection': 'keep-alive',
    'Host': 'apkcombo.com',
}
APKCOMBO_ASSET_SUFFIXES = ('.apk', '.apks', '.xapk')
SOURCEFORGE_MIRROR_LIMIT = 8
ONEDRIVE_BADGER_URL = 'https://api-badgerp.svc.ms/v1.0/token'
ONEDRIVE_PERSONAL_SHARES_URL = 'https://my.microsoftpersonalcontent.com/_api/v2.0/shares'
ONEDRIVE_APP_ID = '1141147648'
ONEDRIVE_APP_UUID = '5cbed6ac-a083-4e14-b191-b4ba07653de2'
ONEDRIVE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0'
}


@dataclass(frozen=True)
class SourceForgeInput:
    project: str
    filename: str

    @property
    def name(self) -> str:
        return self.filename.rsplit('/', 1)[-1]


@dataclass(frozen=True)
class GitHubReleaseInput:
    owner: str
    repo: str
    tag: str
    asset: str

    @property
    def name(self) -> str:
        return self.asset.rsplit('/', 1)[-1]


@dataclass(frozen=True)
class OneDriveAccess:
    cid: str = ''
    resid: str = ''
    authkey: str = ''
    redeem: str = ''


class MediaFireParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.download_url = ''
        self.download_text = ''
        self.filename = ''
        self._in_download_link = False
        self._in_filename = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == 'a' and values.get('aria-label') == 'Download file':
            self.download_url = values.get('href') or ''
            self._in_download_link = True
        elif tag == 'div' and 'filename' in (values.get('class') or '').split():
            self._in_filename = True

    def handle_endtag(self, tag: str) -> None:
        if tag == 'a':
            self._in_download_link = False
        elif tag == 'div':
            self._in_filename = False

    def handle_data(self, data: str) -> None:
        if self._in_download_link:
            self.download_text += data
        elif self._in_filename and not self.filename.strip() and data.strip():
            self.filename = data.strip()


class SourceForgeMirrorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.mirrors: list[tuple[str, str]] = []
        self._in_mirror_list = False
        self._active_mirror = ''
        self._active_text = ''

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == 'ul' and values.get('id') == 'mirrorList':
            self._in_mirror_list = True
        elif self._in_mirror_list and tag == 'li':
            self._active_mirror = values.get('id') or ''
            self._active_text = ''

    def handle_endtag(self, tag: str) -> None:
        if tag == 'ul':
            self._in_mirror_list = False
        elif self._in_mirror_list and tag == 'li' and self._active_mirror:
            name = re.sub(r'\s+', ' ', self._active_text).strip()
            self.mirrors.append((self._active_mirror, name))
            self._active_mirror = ''
            self._active_text = ''

    def handle_data(self, data: str) -> None:
        if self._active_mirror:
            self._active_text += data


class FDroidParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.links: list[DirectLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != 'a':
            return

        href = dict(attrs).get('href') or ''
        if not href.endswith('.apk') or '/repo/' not in href:
            return

        url = urljoin(self.page_url, href)
        self.links.append(DirectLink(name=unquote(urlparse(url).path.rsplit('/', 1)[-1]), url=url))


class IzzyOnDroidParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.links: list[DirectLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != 'a':
            return

        href = dict(attrs).get('href') or ''
        if not href.endswith('.apk') or '/fdroid/repo/' not in href:
            return

        url = urljoin(self.page_url, href)
        self.links.append(DirectLink(name=unquote(urlparse(url).path.rsplit('/', 1)[-1]), url=url))


class APKComboParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.links: list[DirectLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != 'a':
            return

        values = dict(attrs)
        if 'variant' not in (values.get('class') or '').split():
            return

        if direct_url := parse_apkcombo_download_url(values.get('href') or '', self.page_url):
            self.links.append(DirectLink(name=parse_apkcombo_filename(direct_url), url=direct_url))


def parse_sourceforge_input(url: str) -> SourceForgeInput | None:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip('/').split('/')]
    if len(parts) < 5 or parts[0] not in ('projects', 'p') or parts[2] != 'files':
        return None

    project = parts[1]
    filename_parts = parts[3:]
    if filename_parts[-1] == 'download':
        filename_parts = filename_parts[:-1]
    filename = '/'.join(part.strip('/') for part in filename_parts if part.strip('/'))
    return SourceForgeInput(project, filename) if project and filename else None


def parse_github_release_input(url: str) -> GitHubReleaseInput | None:
    parsed = urlparse(url)
    if parsed.netloc != 'github.com':
        return None

    parts = [unquote(part) for part in parsed.path.strip('/').split('/')]
    if len(parts) < 6 or parts[2:4] != ['releases', 'download']:
        return None

    return GitHubReleaseInput(parts[0], parts[1], parts[4], '/'.join(parts[5:]))


def parse_pixeldrain_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc not in ('pixeldrain.com', 'www.pixeldrain.com'):
        return None

    parts = [part for part in parsed.path.strip('/').split('/') if part]
    if len(parts) >= 2 and parts[0] in ('u', 'file'):
        return parts[1]
    return None


def convert_dropbox_url(url: str) -> DirectLink | None:
    parsed = urlparse(url)
    if parsed.netloc not in ('dropbox.com', 'www.dropbox.com'):
        return None

    query = parse_qs(parsed.query)
    query['dl'] = ['1']
    direct_url = urlunparse(
        parsed._replace(netloc='dl.dropboxusercontent.com', query=urlencode(query, doseq=True))
    )
    return DirectLink(
        name=unquote(parsed.path.rstrip('/').rsplit('/', 1)[-1] or 'dropbox'), url=direct_url
    )


def build_onedrive_share_id(url: str) -> str:
    return urlsafe_b64encode(url.encode()).decode().rstrip('=')


def parse_onedrive_access(url: str) -> OneDriveAccess:
    query = parse_qs(urlparse(url).query)
    resid = (query.get('resid') or query.get('id') or [''])[0]
    cid = (query.get('cid') or [''])[0] or resid.split('!', 1)[0]
    return OneDriveAccess(
        cid=cid,
        resid=resid,
        authkey=(query.get('authkey') or [''])[0],
        redeem=(query.get('redeem') or [''])[0],
    )


def parse_onedrive_link(payload: dict, share_url: str) -> DirectLink | None:
    download_url = payload.get('@content.downloadUrl')
    if not download_url:
        return None

    return DirectLink(
        name=str(payload.get('name') or urlparse(share_url).path.rsplit('/', 1)[-1] or 'onedrive'),
        url=str(download_url),
        size=str(payload.get('size') or ''),
    )


def parse_yandex_disk_link(
    metadata_payload: dict, download_payload: dict, share_url: str
) -> DirectLink | None:
    download_url = download_payload.get('href')
    if not download_url:
        return None

    return DirectLink(
        name=str(
            metadata_payload.get('name')
            or urlparse(share_url).path.rsplit('/', 1)[-1]
            or 'yandex-disk'
        ),
        url=str(download_url),
        size=str(metadata_payload.get('size') or ''),
    )


def parse_fdroid_link(url: str) -> DirectLink | None:
    parsed = urlparse(url)
    if parsed.netloc not in ('f-droid.org', 'www.f-droid.org'):
        return None

    if parsed.path.startswith('/repo/') and parsed.path.endswith('.apk'):
        return DirectLink(name=unquote(parsed.path.rsplit('/', 1)[-1]), url=url)
    return None


def parse_fdroid_links(html: str, page_url: str) -> list[DirectLink]:
    parser = FDroidParser(page_url)
    parser.feed(html)
    return parser.links[:1]


def parse_izzyondroid_link(url: str) -> DirectLink | None:
    parsed = urlparse(url)
    if parsed.netloc != 'apt.izzysoft.de':
        return None

    if parsed.path.startswith('/fdroid/repo/') and parsed.path.endswith('.apk'):
        return DirectLink(name=unquote(parsed.path.rsplit('/', 1)[-1]), url=url)
    return None


def parse_izzyondroid_links(html: str, page_url: str) -> list[DirectLink]:
    parser = IzzyOnDroidParser(page_url)
    parser.feed(html)
    return parser.links[:1]


def parse_apkpure_app_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.removeprefix('www.').removeprefix('m.') not in ('apkpure.com', 'apkpure.net'):
        return None

    path_parts = [part for part in parsed.path.strip('/').split('/') if part]
    app_id = path_parts[-1] if path_parts else ''
    return app_id if re.fullmatch(APKPURE_APP_ID_PATTERN, app_id) else None


def parse_apkpure_links(payload: dict) -> list[DirectLink]:
    latest_version = ''
    links: list[DirectLink] = []
    seen_urls: set[str] = set()
    for version in payload.get('version_list', []):
        if not latest_version:
            latest_version = str(version.get('version_name') or '')
        elif version.get('version_name') != latest_version:
            break

        asset = version.get('asset') or {}
        download_url = str(asset.get('url') or '')
        if not download_url or download_url in seen_urls:
            continue

        asset_type = str(asset.get('type') or 'APK').lower()
        name = str(
            asset.get('name') or version.get('title') or version.get('package_name') or 'apkpure'
        )
        version_name = str(version.get('version_name') or '').strip()
        version_code = str(version.get('version_code') or '').strip()
        if version_name:
            name = f'{name}_{version_name}'
        if version_code:
            name = f'{name}_{version_code}'
        links.append(
            DirectLink(
                name=f'{name}.{asset_type}', url=download_url, size=str(asset.get('size') or '')
            )
        )
        seen_urls.add(download_url)
    return links[:1]


def parse_aptoide_link(url: str) -> DirectLink | None:
    parsed = urlparse(url)
    if not parsed.netloc.endswith('.aptoide.com'):
        return None

    if parsed.path.endswith('.apk'):
        return DirectLink(name=unquote(parsed.path.rsplit('/', 1)[-1]), url=url)
    return None


def parse_aptoide_app_id(html: str) -> str:
    if match := re.search(APTOIDE_APP_ID_PATTERN, html):
        return match.group(1)
    return ''


def parse_aptoide_links(payload: dict) -> list[DirectLink]:
    data = ((payload.get('nodes') or {}).get('meta') or {}).get('data') or {}
    file_payload = data.get('file') or {}
    download_url = str(file_payload.get('path') or '')
    if not download_url:
        return []

    name = str(data.get('name') or data.get('package') or 'aptoide')
    version = str(file_payload.get('vername') or '').strip()
    version_code = str(file_payload.get('vercode') or '').strip()
    if version:
        name = f'{name}_{version}'
    if version_code:
        name = f'{name}_{version_code}'
    return [
        DirectLink(
            name=f'{name}.apk',
            url=download_url,
            size=str(file_payload.get('filesize') or data.get('size') or ''),
        )
    ]


def parse_huawei_appgallery_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc not in (
        'appgallery.huawei.com',
        'www.appgallery.huawei.com',
        'appgallery.cloud.huawei.com',
    ):
        return ''

    path = parsed.fragment or parsed.path
    parts = [part for part in path.strip('/').split('/') if part]
    if (
        len(parts) >= 2
        and parts[0] in ('app', 'appdl')
        and re.fullmatch(HUAWEI_APPGALLERY_ID_PATTERN, parts[1])
    ):
        return parts[1]
    return ''


def build_huawei_appgallery_download_url(app_id: str) -> str:
    return f'{HUAWEI_APPGALLERY_DOWNLOAD_URL}/{app_id}'


def parse_huawei_appgallery_redirect(location: str) -> DirectLink | None:
    filename = unquote(urlparse(location).path.rsplit('/', 1)[-1])
    if not filename.endswith('.apk'):
        return None

    parts = filename.rsplit('.', 2)
    name = '.'.join(parts[:-2]) if len(parts) >= 3 else filename.removesuffix('.apk')
    return DirectLink(name=f'{name}.apk', url=location)


def parse_apkcombo_app_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc not in ('apkcombo.com', 'www.apkcombo.com'):
        return ''

    parts = [part for part in parsed.path.strip('/').split('/') if part]
    if len(parts) < 2:
        return ''
    return urlunparse(('https', 'apkcombo.com', f'/{parts[0]}/{parts[1]}', '', '', ''))


def parse_apkcombo_download_url(href: str, page_url: str) -> str:
    url = urljoin(page_url, href)
    parsed = urlparse(url)
    if parsed.netloc in ('apkcombo.com', 'www.apkcombo.com') and parsed.path == '/r2':
        url = (parse_qs(parsed.query).get('u') or [''])[0]
        parsed = urlparse(url)

    if parsed.path.lower().endswith(APKCOMBO_ASSET_SUFFIXES):
        return url
    return ''


def parse_apkcombo_filename(url: str) -> str:
    parsed = urlparse(url)
    content_disposition = (parse_qs(parsed.query).get('response-content-disposition') or [''])[0]
    if match := re.search(r'filename="?([^";]+)', content_disposition):
        return unquote(match.group(1))
    return unquote(parsed.path.rsplit('/', 1)[-1] or 'apkcombo')


def parse_apkcombo_links(html: str, page_url: str) -> list[DirectLink]:
    parser = APKComboParser(page_url)
    parser.feed(html)
    links: list[DirectLink] = []
    seen_urls: set[str] = set()
    for link in parser.links:
        if link.url not in seen_urls:
            links.append(link)
            seen_urls.add(link.url)
    return links


def quote_url_path(path: str) -> str:
    return '/'.join(url_quote(part) for part in path.split('/'))


def parse_mediafire_links(html: str) -> DirectLink | None:
    parser = MediaFireParser()
    parser.feed(html)
    if not parser.download_url:
        return None

    size_match = re.search(r'\(([^)]+)\)', parser.download_text)
    return DirectLink(
        name=parser.filename.strip() or parser.download_url.rsplit('/', 1)[-1],
        url=parser.download_url,
        size=size_match.group(1).strip() if size_match else '',
    )


def parse_sourceforge_mirrors(html: str, sourceforge_input: SourceForgeInput) -> list[DirectLink]:
    parser = SourceForgeMirrorParser()
    parser.feed(html)
    links = []
    for mirror_id, mirror_text in parser.mirrors:
        if mirror_id == 'autoselect':
            continue
        mirror_name = re.search(r'\(([^)]+)\)', mirror_text)
        links.append(
            DirectLink(
                name=mirror_name.group(1) if mirror_name else mirror_id,
                url=(
                    f'https://{mirror_id}.dl.sourceforge.net/project/'
                    f'{url_quote(sourceforge_input.project)}/{quote_url_path(sourceforge_input.filename)}'
                ),
            )
        )
    return links[:SOURCEFORGE_MIRROR_LIMIT]


async def resolve_mediafire(url: str) -> list[DirectLink]:
    async with aiohttp.ClientSession() as session, session.get(url) as response:
        response.raise_for_status()
        direct_link = parse_mediafire_links(await response.text())
    return [direct_link] if direct_link else []


async def resolve_sourceforge(url: str) -> list[DirectLink]:
    sourceforge_input = parse_sourceforge_input(url)
    if not sourceforge_input:
        return []

    async with (
        aiohttp.ClientSession() as session,
        session.get(
            'https://sourceforge.net/settings/mirror_choices',
            params={
                'projectname': sourceforge_input.project,
                'filename': f'/{sourceforge_input.filename}',
            },
        ) as response,
    ):
        response.raise_for_status()
        return parse_sourceforge_mirrors(await response.text(), sourceforge_input)


async def resolve_pixeldrain(url: str) -> list[DirectLink]:
    file_id = parse_pixeldrain_file_id(url)
    if not file_id:
        return []

    async with (
        aiohttp.ClientSession() as session,
        session.get(f'https://pixeldrain.com/api/file/{file_id}/info') as response,
    ):
        if response.status == 404:
            return []
        response.raise_for_status()
        payload = await response.json()

    if not payload.get('success', True):
        return []

    return [
        DirectLink(
            name=str(payload.get('name') or file_id),
            url=f'https://pixeldrain.com/api/file/{file_id}',
            size=str(payload.get('size') or ''),
        )
    ]


async def resolve_github_release(url: str) -> list[DirectLink]:
    release_input = parse_github_release_input(url)
    if not release_input:
        return []

    async with (
        aiohttp.ClientSession() as session,
        session.head(url, allow_redirects=False) as response,
    ):
        if response.status in (301, 302, 303, 307, 308) and response.headers.get('location'):
            return [DirectLink(name=release_input.name, url=response.headers['location'])]
        response.raise_for_status()

    return [DirectLink(name=release_input.name, url=url)]


async def resolve_archive(url: str) -> list[DirectLink]:
    archive_input = extract_archive_input(url)
    if not archive_input:
        return []

    files = await fetch_archive_files(archive_input.identifier)
    return [
        DirectLink(name=archive_file.name, url=archive_file.download_url(archive_input.identifier))
        for archive_file in select_archive_files(files, archive_input.selected_path)
    ]


async def resolve_dropbox(url: str) -> list[DirectLink]:
    direct_link = convert_dropbox_url(url)
    return [direct_link] if direct_link else []


async def resolve_gdrive(url: str) -> list[DirectLink]:
    file_id = extract_gdrive_file_id(url)
    if not file_id:
        return []

    direct_url = build_gdrive_direct_url(file_id)
    async with aiohttp.ClientSession() as session, session.get(direct_url) as response:
        response.raise_for_status()
        if response.content_type != 'text/html':
            return [DirectLink(name=file_id, url=direct_url)]

        confirm = parse_gdrive_confirm_token(await response.text())

    if not confirm:
        return []
    return [DirectLink(name=file_id, url=build_gdrive_direct_url(file_id, confirm))]


async def resolve_fdroid(url: str) -> list[DirectLink]:
    if direct_link := parse_fdroid_link(url):
        return [direct_link]

    async with aiohttp.ClientSession() as session, session.get(url) as response:
        if response.status == 404:
            return []
        response.raise_for_status()
        return parse_fdroid_links(await response.text(), url)


async def resolve_izzyondroid(url: str) -> list[DirectLink]:
    if direct_link := parse_izzyondroid_link(url):
        return [direct_link]

    async with aiohttp.ClientSession() as session, session.get(url) as response:
        if response.status == 404:
            return []
        response.raise_for_status()
        return parse_izzyondroid_links(await response.text(), url)


async def resolve_apkpure(url: str) -> list[DirectLink]:
    app_id = parse_apkpure_app_id(url)
    if not app_id:
        return []

    async with (
        aiohttp.ClientSession(headers=APKPURE_HEADERS) as session,
        session.get(APKPURE_API_URL, params={'package_name': app_id, 'hl': 'en'}) as response,
    ):
        if response.status == 404:
            return []
        response.raise_for_status()
        return parse_apkpure_links(await response.json())


async def resolve_aptoide(url: str) -> list[DirectLink]:
    if direct_link := parse_aptoide_link(url):
        return [direct_link]

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 404:
                return []
            response.raise_for_status()
            app_id = parse_aptoide_app_id(await response.text())

        if not app_id:
            return []

        async with session.get(f'https://ws2.aptoide.com/api/7/getApp/app_id/{app_id}') as response:
            if response.status == 404:
                return []
            response.raise_for_status()
            return parse_aptoide_links(await response.json())


async def resolve_huawei_appgallery(url: str) -> list[DirectLink]:
    app_id = parse_huawei_appgallery_id(url)
    if not app_id:
        return []

    download_url = build_huawei_appgallery_download_url(app_id)
    async with (
        aiohttp.ClientSession() as session,
        session.get(download_url, allow_redirects=False) as response,
    ):
        if response.status in (301, 302, 303, 307, 308) and response.headers.get('location'):
            direct_link = parse_huawei_appgallery_redirect(response.headers['location'])
            return [direct_link] if direct_link else []
        if response.status == 404:
            return []
        response.raise_for_status()

    return [DirectLink(name=f'{app_id}.apk', url=download_url)]


async def resolve_apkcombo(url: str) -> list[DirectLink]:
    standard_url = parse_apkcombo_app_url(url)
    if not standard_url:
        return []

    async with (
        aiohttp.ClientSession(headers=APKCOMBO_HEADERS) as session,
        session.get(f'{standard_url}/download/apk') as response,
    ):
        if response.status == 404:
            return []
        response.raise_for_status()
        return parse_apkcombo_links(await response.text(), standard_url)


async def fetch_onedrive_badger_token(session: aiohttp.ClientSession) -> str:
    async with session.post(
        ONEDRIVE_BADGER_URL,
        headers={**ONEDRIVE_HEADERS, 'AppId': ONEDRIVE_APP_ID},
        json={'appId': ONEDRIVE_APP_UUID},
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    return str(payload.get('token') or '')


async def resolve_onedrive_personal(url: str) -> DirectLink | None:
    async with aiohttp.ClientSession(headers=ONEDRIVE_HEADERS) as session:
        async with session.get(url, allow_redirects=True) as response:
            access = parse_onedrive_access(str(response.url))
        if not access.redeem:
            return None

        token = await fetch_onedrive_badger_token(session)
        if not token:
            return None

        async with session.get(
            f'{ONEDRIVE_PERSONAL_SHARES_URL}/u!{access.redeem}/driveitem',
            headers={
                **ONEDRIVE_HEADERS,
                'Authorization': f'Badger {token}',
                'Prefer': 'autoredeem',
            },
        ) as response:
            if response.status in (401, 403, 404):
                return None
            response.raise_for_status()
            return parse_onedrive_link(await response.json(), url)


async def resolve_onedrive_legacy(url: str) -> DirectLink | None:
    share_id = build_onedrive_share_id(url)
    async with (
        aiohttp.ClientSession() as session,
        session.get(f'https://api.onedrive.com/v1.0/shares/u!{share_id}/root') as response,
    ):
        if response.status in (401, 403, 404):
            return None
        response.raise_for_status()
        return parse_onedrive_link(await response.json(), url)


async def resolve_onedrive(url: str) -> list[DirectLink]:
    direct_link = await resolve_onedrive_personal(url) or await resolve_onedrive_legacy(url)
    return [direct_link] if direct_link else []


async def resolve_yandex_disk(url: str) -> list[DirectLink]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            'https://cloud-api.yandex.net/v1/disk/public/resources',
            params={'public_key': url},
        ) as metadata_response:
            if metadata_response.status == 404:
                return []
            metadata_response.raise_for_status()
            metadata_payload = await metadata_response.json()

        async with session.get(
            'https://cloud-api.yandex.net/v1/disk/public/resources/download',
            params={'public_key': url},
        ) as download_response:
            if download_response.status == 404:
                return []
            download_response.raise_for_status()
            direct_link = parse_yandex_disk_link(
                metadata_payload, await download_response.json(), url
            )

    return [direct_link] if direct_link else []
