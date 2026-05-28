import os
from asyncio import sleep
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qs, urlparse
from zipfile import ZIP_DEFLATED, ZipFile

import aiohttp
import orjson
import regex as re

from src import STATE_DIR, TMP_DIR
from src.utils.patterns import HTTP_URL_PATTERN

os.environ.setdefault('PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION', 'python')

from gpapi import googleplay_pb2

DISPENSER_URL_ENV = 'GPLAY_DISPENSER_URL'
FDFE_URL = 'https://android.clients.google.com/fdfe'
PURCHASE_URL = f'{FDFE_URL}/purchase'
DELIVERY_URL = f'{FDFE_URL}/delivery'
DETAILS_URL = f'{FDFE_URL}/details'
GPLAY_STATE_DIR = STATE_DIR / 'gplay'
DFE_ENCODED_TARGETS = (
    'CAESN/qigQYC2AMBFfUbyA7SM5Ij/CvfBoIDgxXrBPsDlQUdMfOLAfoFrwEHgAcBrQYhoA0cGt4MKK0Y2gI'
)

PACKAGE_PATTERN = re.compile(r'^[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+$')
PLAY_HOSTS = {'play.google.com', 'www.play.google.com'}
ARCH_ALIASES = {
    'arm64': 'arm64-v8a',
    'arm64-v8a': 'arm64-v8a',
    'armv7': 'armeabi-v7a',
    'armeabi-v7a': 'armeabi-v7a',
}
ARCH_LABELS = {
    'arm64-v8a': 'ARM64',
    'armeabi-v7a': 'ARMv7',
}

BASE_PROFILE = {
    'Build.RADIO': 'unknown',
    'Build.BOOTLOADER': 'unknown',
    'Build.TYPE': 'user',
    'Build.TAGS': 'release-keys',
    'Screen.Density': '420',
    'Screen.Width': '1080',
    'Screen.Height': '2400',
    'Locales': 'en-US',
    'Features': (
        'android.hardware.touchscreen,android.hardware.faketouch,android.hardware.location,'
        'android.hardware.wifi,android.hardware.bluetooth,android.hardware.camera,'
        'android.hardware.microphone,android.software.webview,'
        'com.google.android.feature.GOOGLE_BUILD,com.google.android.feature.GOOGLE_EXPERIENCE'
    ),
    'SharedLibraries': (
        'android.ext.shared,com.google.android.gms,com.google.android.maps,org.apache.http.legacy'
    ),
    'GSF.version': '243433000',
    'Vending.version': '84122900',
    'Vending.versionString': '41.2.29-23 [0] [PR] 639844241',
    'Roaming': 'mobile-notroaming',
    'TimeZone': 'UTC',
    'CellOperator': '310260',
    'SimOperator': '310260',
    'Client': 'android-google',
    'GL.Version': '196610',
    'GL.Extensions': 'GL_OES_EGL_image',
}
PROFILES = {
    'arm64-v8a': [
        {
            **BASE_PROFILE,
            'UserReadableName': 'Generic Pixel 8 ARM64',
            'Build.HARDWARE': 'shiba',
            'Build.FINGERPRINT': 'google/shiba/shiba:14/UD1A.230803.041/10808477:user/release-keys',
            'Build.BRAND': 'google',
            'Build.DEVICE': 'shiba',
            'Build.VERSION.SDK_INT': '34',
            'Build.VERSION.RELEASE': '14',
            'Build.MODEL': 'Pixel 8',
            'Build.MANUFACTURER': 'Google',
            'Build.PRODUCT': 'shiba',
            'Build.ID': 'UD1A.230803.041',
            'Build.SUPPORTED_ABIS': 'arm64-v8a,armeabi-v7a,armeabi',
            'Platforms': 'arm64-v8a,armeabi-v7a,armeabi',
        },
        {
            **BASE_PROFILE,
            'UserReadableName': 'Generic Pixel 7a ARM64',
            'Build.HARDWARE': 'lynx',
            'Build.FINGERPRINT': 'google/lynx/lynx:14/UQ1A.231205.015/11073148:user/release-keys',
            'Build.BRAND': 'google',
            'Build.DEVICE': 'lynx',
            'Build.VERSION.SDK_INT': '34',
            'Build.VERSION.RELEASE': '14',
            'Build.MODEL': 'Pixel 7a',
            'Build.MANUFACTURER': 'Google',
            'Build.PRODUCT': 'lynx',
            'Build.ID': 'UQ1A.231205.015',
            'Build.SUPPORTED_ABIS': 'arm64-v8a,armeabi-v7a,armeabi',
            'Platforms': 'arm64-v8a,armeabi-v7a,armeabi',
        },
        {
            **BASE_PROFILE,
            'UserReadableName': 'Generic Galaxy S23 ARM64',
            'Build.HARDWARE': 'qcom',
            'Build.FINGERPRINT': 'samsung/dm1qxxx/dm1q:14/UP1A.231005.007/S911BXXS3BWKC:user/release-keys',
            'Build.BRAND': 'samsung',
            'Build.DEVICE': 'dm1q',
            'Build.VERSION.SDK_INT': '34',
            'Build.VERSION.RELEASE': '14',
            'Build.MODEL': 'SM-S911B',
            'Build.MANUFACTURER': 'samsung',
            'Build.PRODUCT': 'dm1qxxx',
            'Build.ID': 'UP1A.231005.007',
            'Build.SUPPORTED_ABIS': 'arm64-v8a,armeabi-v7a,armeabi',
            'Platforms': 'arm64-v8a,armeabi-v7a,armeabi',
        },
    ],
    'armeabi-v7a': [
        {
            **BASE_PROFILE,
            'UserReadableName': 'Generic Galaxy J5 ARMv7',
            'Build.HARDWARE': 'qcom',
            'Build.FINGERPRINT': 'samsung/j5y17ltexx/j5y17lte:8.1.0/M1AJQ/J530FXXU7CTF1:user/release-keys',
            'Build.BRAND': 'samsung',
            'Build.DEVICE': 'j5y17lte',
            'Build.VERSION.SDK_INT': '27',
            'Build.VERSION.RELEASE': '8.1.0',
            'Build.MODEL': 'SM-J530F',
            'Build.MANUFACTURER': 'samsung',
            'Build.PRODUCT': 'j5y17ltexx',
            'Build.ID': 'M1AJQ',
            'Build.SUPPORTED_ABIS': 'armeabi-v7a,armeabi',
            'Platforms': 'armeabi-v7a,armeabi',
        },
        {
            **BASE_PROFILE,
            'UserReadableName': 'Generic Moto G5 ARMv7',
            'Build.HARDWARE': 'qcom',
            'Build.FINGERPRINT': 'motorola/cedric/cedric:8.1.0/OPP28.85-19-4/6:user/release-keys',
            'Build.BRAND': 'motorola',
            'Build.DEVICE': 'cedric',
            'Build.VERSION.SDK_INT': '27',
            'Build.VERSION.RELEASE': '8.1.0',
            'Build.MODEL': 'Moto G5',
            'Build.MANUFACTURER': 'motorola',
            'Build.PRODUCT': 'cedric',
            'Build.ID': 'OPP28.85-19-4',
            'Build.SUPPORTED_ABIS': 'armeabi-v7a,armeabi',
            'Platforms': 'armeabi-v7a,armeabi',
        },
    ],
}


@dataclass(frozen=True)
class GPlaySplit:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class GPlayDownloadInfo:
    package: str
    title: str
    version: str
    version_code: int
    arch: str
    url: str
    size: int
    cookies: dict[str, str]
    splits: list[GPlaySplit]


@dataclass(frozen=True)
class GPlayDownloaded:
    info: GPlayDownloadInfo
    path: Path
    files_count: int


class GPlayError(Exception):
    pass


class GPlayPaidAppError(GPlayError):
    pass


class GPlayRetryAuthError(GPlayError):
    pass


def normalize_arch(arch: str) -> str:
    normalized = ARCH_ALIASES.get(arch)
    if not normalized:
        raise ValueError(f'Unsupported architecture: {arch}')
    return normalized


def arch_label(arch: str) -> str:
    return ARCH_LABELS[normalize_arch(arch)]


def extract_gplay_package(text: str) -> str:
    text = text.strip()
    command_match = re.match(r'^/gplay(?:@\w+)?(?:\s+(.+))?$', text)
    if command_match:
        text = (command_match.group(1) or '').strip()

    if PACKAGE_PATTERN.fullmatch(text):
        return text

    if text.startswith('market://details?'):
        package = (parse_qs(urlparse(text).query).get('id') or [''])[0]
        return package if PACKAGE_PATTERN.fullmatch(package) else ''

    for match in re.finditer(HTTP_URL_PATTERN, text):
        parsed = urlparse(match.group(0).rstrip('.,)'))
        if parsed.netloc in PLAY_HOSTS and parsed.path == '/store/apps/details':
            package = (parse_qs(parsed.query).get('id') or [''])[0]
            if PACKAGE_PATTERN.fullmatch(package):
                return package
    return ''


def has_gplay_link(text: str) -> bool:
    return bool(extract_gplay_package(text))


def get_dispenser_url() -> str:
    return os.getenv(DISPENSER_URL_ENV, '').strip().rstrip('/')


def auth_cache_path(arch: str) -> Path:
    return GPLAY_STATE_DIR / f'auth-{normalize_arch(arch)}.json'


def load_cached_auth(arch: str) -> dict[str, Any] | None:
    path = auth_cache_path(arch)
    if not path.exists():
        return None
    try:
        auth = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError:
        return None
    return auth if auth.get('authToken') and auth.get('gsfId') else None


def save_cached_auth(arch: str, auth: dict[str, Any]) -> None:
    path = auth_cache_path(arch)
    path.parent.mkdir(exist_ok=True)
    tmp_path = path.with_suffix('.tmp')
    tmp_path.write_bytes(orjson.dumps(auth, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    tmp_path.chmod(0o600)
    tmp_path.replace(path)


def clear_cached_auth(arch: str) -> None:
    auth_cache_path(arch).unlink(missing_ok=True)


def auth_headers(auth: dict[str, Any], accept_language: str = 'en-US') -> dict[str, str]:
    device_info = auth.get('deviceInfoProvider') or {}
    headers = {
        'Authorization': f'Bearer {auth.get("authToken", "")}',
        'User-Agent': str(device_info.get('userAgentString') or ''),
        'X-DFE-Device-Id': str(auth.get('gsfId') or ''),
        'Accept-Language': accept_language,
        'X-DFE-Encoded-Targets': DFE_ENCODED_TARGETS,
        'X-DFE-Client-Id': 'am-android-google',
        'X-DFE-Network-Type': '4',
        'X-DFE-Content-Filters': '',
        'X-Limit-Ad-Tracking-Enabled': 'false',
        'X-Ad-Id': '',
        'X-DFE-UserLanguages': accept_language.replace('-', '_'),
        'X-DFE-Request-Params': 'timeoutMs=4000',
        'X-DFE-Cookie': str(auth.get('dfeCookie') or ''),
        'X-DFE-No-Prefetch': 'true',
        'Content-Type': 'application/x-protobuf',
        'Accept': 'application/x-protobuf',
    }
    if auth.get('deviceCheckInConsistencyToken'):
        headers['X-DFE-Device-Checkin-Consistency-Token'] = auth['deviceCheckInConsistencyToken']
    if auth.get('deviceConfigToken'):
        headers['X-DFE-Device-Config-Token'] = auth['deviceConfigToken']
    if device_info.get('mccMnc'):
        headers['X-DFE-MCCMNC'] = device_info['mccMnc']
    return headers


def cookie_header(cookies: dict[str, str]) -> dict[str, str]:
    return (
        {'Cookie': '; '.join(f'{name}={value}' for name, value in cookies.items())}
        if cookies
        else {}
    )


async def request_anonymous_auth(
    session: aiohttp.ClientSession, profile: dict[str, str]
) -> dict[str, Any]:
    dispenser_url = get_dispenser_url()
    if not dispenser_url:
        raise GPlayError(f'{DISPENSER_URL_ENV} is not configured')

    async with session.post(
        dispenser_url,
        json=profile,
        headers={
            'User-Agent': 'com.aurora.store-4.6.1-70',
            'Content-Type': 'application/json',
        },
    ) as response:
        if response.status != 200:
            raise GPlayRetryAuthError(f'Anonymous auth failed with HTTP {response.status}')
        auth = await response.json()
    if not auth.get('authToken') or not auth.get('gsfId'):
        raise GPlayRetryAuthError('Anonymous auth did not return a usable token')
    return auth


def parse_wrapper(content: bytes) -> Any:
    wrapper = googleplay_pb2.ResponseWrapper()
    wrapper.ParseFromString(content)
    return wrapper


async def fetch_download_info(
    session: aiohttp.ClientSession,
    package: str,
    arch: str,
    auth: dict[str, Any],
) -> GPlayDownloadInfo:
    arch = normalize_arch(arch)
    headers = auth_headers(auth)
    async with session.get(f'{DETAILS_URL}?doc={package}', headers=headers) as response:
        content = await response.read()
        if response.status in (401, 403, 429):
            raise GPlayRetryAuthError(f'Google Play details failed with HTTP {response.status}')
        if response.status == 404:
            raise GPlayError(f'App not found: {package}')
        if response.status != 200:
            raise GPlayError(f'Google Play details failed with HTTP {response.status}')

    details = parse_wrapper(content).payload.detailsResponse.docV2
    if not details.docid:
        raise GPlayError('App not found or not available for this device profile')

    app_details = details.details.appDetails
    version_code = app_details.versionCode
    version = app_details.versionString
    title = details.title or package

    for offer in details.offer:
        if offer.offerType == 1 and offer.micros > 0:
            raise GPlayPaidAppError(f'This app is not free ({offer.formattedAmount or "paid"})')

    purchase_headers = {**headers, 'Content-Type': 'application/x-www-form-urlencoded'}
    async with session.post(
        PURCHASE_URL,
        headers=purchase_headers,
        data=f'doc={package}&ot=1&vc={version_code}',
    ) as response:
        await response.read()

    async with session.get(
        f'{DELIVERY_URL}?doc={package}&ot=1&vc={version_code}',
        headers=headers,
    ) as response:
        content = await response.read()
        if response.status in (401, 403, 429):
            raise GPlayRetryAuthError(f'Google Play delivery failed with HTTP {response.status}')
        if response.status != 200:
            raise GPlayError(f'Google Play delivery failed with HTTP {response.status}')

    delivery = parse_wrapper(content).payload.deliveryResponse.appDeliveryData
    if not delivery.downloadUrl:
        raise GPlayError('No download URL available. The app may be region-restricted.')

    cookies = {cookie.name: cookie.value for cookie in delivery.downloadAuthCookie}
    splits = [
        GPlaySplit(name=split.name or f'split{idx}', url=split.downloadUrl, size=split.size)
        for idx, split in enumerate(delivery.split)
        if split.downloadUrl
    ]
    return GPlayDownloadInfo(
        package=package,
        title=title,
        version=version,
        version_code=version_code,
        arch=arch,
        url=delivery.downloadUrl,
        size=delivery.downloadSize,
        cookies=cookies,
        splits=splits,
    )


async def get_download_info(
    session: aiohttp.ClientSession,
    package: str,
    arch: str,
) -> GPlayDownloadInfo:
    arch = normalize_arch(arch)
    last_error: Exception | None = None
    if cached_auth := load_cached_auth(arch):
        try:
            return await fetch_download_info(session, package, arch, cached_auth)
        except GPlayRetryAuthError as e:
            clear_cached_auth(arch)
            last_error = e

    for idx, profile in enumerate(PROFILES[arch]):
        if idx:
            await sleep(1)
        try:
            auth = await request_anonymous_auth(session, profile)
            info = await fetch_download_info(session, package, arch, auth)
            save_cached_auth(arch, auth)
            return info
        except GPlayPaidAppError:
            raise
        except GPlayError as e:
            last_error = e

    raise GPlayError(str(last_error or 'Could not get a working anonymous Google Play token'))


async def download_to_path(
    session: aiohttp.ClientSession,
    url: str,
    output_path: Path,
    cookies: dict[str, str],
) -> Path:
    tmp_path = output_path.with_suffix(f'{output_path.suffix}.part')
    async with session.get(url, headers=cookie_header(cookies)) as response:
        if response.status != 200:
            raise GPlayError(f'Download failed with HTTP {response.status}')
        with tmp_path.open('wb') as output:
            async for chunk in response.content.iter_chunked(1024 * 256):
                output.write(chunk)

    expected_size = int(response.headers.get('content-length') or 0)
    if expected_size and tmp_path.stat().st_size != expected_size:
        tmp_path.unlink(missing_ok=True)
        raise GPlayError('Download size mismatch')

    tmp_path.replace(output_path)
    return output_path


def zip_files(files: list[Path], output_path: Path, *, root: Path | None = None) -> Path:
    with ZipFile(output_path, 'w', ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, file.relative_to(root) if root else file.name)
    return output_path


async def download_gplay_arch(
    package: str,
    arch: str,
    output_dir: Path,
    *,
    status: Any = None,
) -> GPlayDownloaded:
    arch = normalize_arch(arch)
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if status:
            await status(
                f'Fetching Google Play delivery info for {package} ({arch_label(arch)})...'
            )
        info = await get_download_info(session, package, arch)
        output_dir.mkdir(parents=True, exist_ok=True)
        base_path = output_dir / f'{package}-{info.version_code}-base.apk'
        files = [base_path]

        if status:
            await status(f'Downloading base APK for {package} ({arch_label(arch)})...')
        await download_to_path(session, info.url, base_path, info.cookies)

        for idx, split in enumerate(info.splits, start=1):
            split_path = output_dir / f'{package}-{info.version_code}-{split.name}.apk'
            files.append(split_path)
            if status:
                await status(f'Downloading split {idx}/{len(info.splits)}: {split.name}')
            await download_to_path(session, split.url, split_path, info.cookies)

    if len(files) == 1:
        final_path = base_path.rename(output_dir / f'{package}-{info.version_code}-{arch}.apk')
    else:
        final_path = zip_files(files, output_dir / f'{package}-{info.version_code}-{arch}.zip')

    return GPlayDownloaded(info=info, path=final_path, files_count=len(files))


async def download_gplay_variants(
    package: str,
    variants: list[str],
    *,
    status: Any = None,
) -> list[GPlayDownloaded]:
    with TemporaryDirectory(dir=TMP_DIR, prefix='gplay-') as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        downloads = []
        for variant in variants:
            arch = normalize_arch(variant)
            downloads.append(
                await download_gplay_arch(package, arch, temp_dir / arch, status=status)
            )
        stable_downloads = []
        for download in downloads:
            stable_path = TMP_DIR / download.path.name
            download.path.replace(stable_path)
            stable_downloads.append(
                GPlayDownloaded(
                    info=download.info,
                    path=stable_path,
                    files_count=download.files_count,
                )
            )
        return stable_downloads
