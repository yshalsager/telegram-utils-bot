import asyncio
from unittest import TestCase

from src.modules.plugins.direct_links import (
    DirectLinks,
    extract_direct_command_input,
    extract_direct_urls,
)
from src.utils.archive_org import ArchiveFile
from src.utils.google_drive import (
    build_gdrive_direct_url,
    extract_gdrive_file_id,
    parse_gdrive_confirm_token,
)
from src.utils.remote_files.models import RemoteFile as DirectLink
from src.utils.remote_files.providers import (
    FOURPDA_COOKIE_FILE,
    GitHubReleaseInput,
    OneDriveAccess,
    SourceForgeInput,
    build_onedrive_share_id,
    convert_dropbox_url,
    parse_apkcombo_app_url,
    parse_apkcombo_download_url,
    parse_apkcombo_filename,
    parse_apkcombo_links,
    parse_apkpure_app_id,
    parse_apkpure_links,
    parse_aptoide_app_id,
    parse_aptoide_link,
    parse_aptoide_links,
    parse_fdroid_link,
    parse_fdroid_links,
    parse_github_release_input,
    parse_huawei_appgallery_id,
    parse_huawei_appgallery_redirect,
    parse_izzyondroid_link,
    parse_izzyondroid_links,
    parse_mediafire_links,
    parse_onedrive_access,
    parse_onedrive_link,
    parse_pixeldrain_file_id,
    parse_sourceforge_input,
    parse_sourceforge_mirrors,
    parse_yandex_disk_link,
    resolve_4pda_attachment,
)
from src.utils.remote_files.resolver import is_supported_remote_url


class DirectLinksInputTest(TestCase):
    def test_extract_direct_urls_returns_multiple_urls_without_trailing_punctuation(self) -> None:
        assert extract_direct_urls(
            'links: https://mediafire.com/file/example/file.zip, '
            'https://sourceforge.net/projects/demo/files/app.zip/download) '
            'https://pixeldrain.com/u/abc123.'
        ) == [
            'https://mediafire.com/file/example/file.zip',
            'https://sourceforge.net/projects/demo/files/app.zip/download',
            'https://pixeldrain.com/u/abc123',
        ]

    def test_direct_command_pattern_accepts_direct_and_reply_forms(self) -> None:
        pattern = DirectLinks.commands['direct'].pattern

        assert pattern.match('/direct')
        assert pattern.match('/direct https://mediafire.com/file/example/file.zip')

    def test_extract_direct_command_input_allows_empty_reply_flow(self) -> None:
        assert extract_direct_command_input('/direct') == ''
        assert (
            extract_direct_command_input('/direct https://mediafire.com/file/example/file.zip')
            == 'https://mediafire.com/file/example/file.zip'
        )

    def test_supported_remote_url_detection_is_provider_only(self) -> None:
        assert is_supported_remote_url(
            'https://sourceforge.net/projects/demo/files/app.zip/download'
        )
        assert is_supported_remote_url('https://4pda.to/forum/dl/post/29739893/usbdeview.zip')
        assert not is_supported_remote_url('https://example.com/file.zip')


class MediaFireParserTest(TestCase):
    def test_parse_mediafire_links_extracts_download_url_name_and_size(self) -> None:
        html = """
            <html>
              <div class="filename">sample.zip</div>
              <a aria-label="Download file" href="https://download.mediafire.com/file/sample.zip">
                Download (12 MB)
              </a>
            </html>
        """

        assert parse_mediafire_links(html) == DirectLink(
            name='sample.zip',
            url='https://download.mediafire.com/file/sample.zip',
            size='12 MB',
        )


class FourPdaParserTest(TestCase):
    def test_resolve_4pda_attachment_uses_private_cookie_file(self) -> None:
        links = asyncio.run(
            resolve_4pda_attachment('https://4pda.to/forum/dl/post/29739893/usbdeview.zip')
        )

        assert links == [
            DirectLink(
                name='usbdeview.zip',
                url='https://4pda.to/forum/dl/post/29739893/usbdeview.zip',
                cookie_file=FOURPDA_COOKIE_FILE,
                source='4pda',
            )
        ]


class SourceForgeParserTest(TestCase):
    def test_parse_sourceforge_input_extracts_project_and_file_path(self) -> None:
        assert parse_sourceforge_input(
            'https://sourceforge.net/projects/demo/files/releases/app%201.0.zip/download'
        ) == SourceForgeInput('demo', 'releases/app 1.0.zip')

    def test_parse_sourceforge_mirrors_builds_direct_mirror_links(self) -> None:
        html = """
            <ul id="mirrorList">
              <li id="autoselect">Auto-select</li>
              <li id="netix">Netherlands (netix)</li>
              <li id="yer">United States (yer)</li>
            </ul>
        """

        assert parse_sourceforge_mirrors(html, SourceForgeInput('demo', 'releases/app.zip')) == [
            DirectLink(
                name='netix',
                url='https://netix.dl.sourceforge.net/project/demo/releases/app.zip',
            ),
            DirectLink(
                name='yer',
                url='https://yer.dl.sourceforge.net/project/demo/releases/app.zip',
            ),
        ]

    def test_parse_sourceforge_mirrors_quotes_file_paths(self) -> None:
        html = """
            <ul id="mirrorList">
              <li id="netix">Netherlands (netix)</li>
            </ul>
        """

        assert parse_sourceforge_mirrors(html, SourceForgeInput('demo', 'app 1.0.zip')) == [
            DirectLink(
                name='netix',
                url='https://netix.dl.sourceforge.net/project/demo/app%201.0.zip',
            )
        ]


class PixeldrainParserTest(TestCase):
    def test_parse_pixeldrain_file_id_accepts_public_file_urls(self) -> None:
        assert parse_pixeldrain_file_id('https://pixeldrain.com/u/abc123') == 'abc123'
        assert parse_pixeldrain_file_id('https://pixeldrain.com/file/abc123') == 'abc123'
        assert parse_pixeldrain_file_id('https://example.com/u/abc123') is None


class GitHubReleaseParserTest(TestCase):
    def test_parse_github_release_input_extracts_asset_details(self) -> None:
        assert parse_github_release_input(
            'https://github.com/owner/repo/releases/download/v1.0.0/app%20mac.zip'
        ) == GitHubReleaseInput('owner', 'repo', 'v1.0.0', 'app mac.zip')

    def test_parse_github_release_input_rejects_non_asset_urls(self) -> None:
        assert (
            parse_github_release_input('https://github.com/owner/repo/releases/tag/v1.0.0') is None
        )


class GoogleDriveParserTest(TestCase):
    def test_extract_gdrive_file_id_accepts_public_single_file_urls(self) -> None:
        assert (
            extract_gdrive_file_id(
                'https://drive.google.com/file/d/abc_123-DEF456/view?usp=sharing'
            )
            == 'abc_123-DEF456'
        )
        assert (
            extract_gdrive_file_id('https://drive.google.com/open?id=abc_123-DEF456')
            == 'abc_123-DEF456'
        )
        assert (
            extract_gdrive_file_id('https://drive.google.com/uc?export=download&id=abc_123-DEF456')
            == 'abc_123-DEF456'
        )

    def test_extract_gdrive_file_id_rejects_folders_and_other_domains(self) -> None:
        assert (
            extract_gdrive_file_id('https://drive.google.com/drive/folders/abc_123-DEF456') is None
        )
        assert extract_gdrive_file_id('https://example.com/open?id=abc_123-DEF456') is None

    def test_build_gdrive_direct_url_uses_download_export(self) -> None:
        assert (
            build_gdrive_direct_url('abc_123-DEF456')
            == 'https://drive.google.com/uc?export=download&id=abc_123-DEF456'
        )
        assert (
            build_gdrive_direct_url('abc_123-DEF456', 'token_123')
            == 'https://drive.google.com/uc?export=download&confirm=token_123&id=abc_123-DEF456'
        )

    def test_parse_gdrive_confirm_token_accepts_href_and_hidden_input_tokens(self) -> None:
        assert (
            parse_gdrive_confirm_token(
                '/uc?export=download&amp;confirm=t_123&amp;id=abc_123-DEF456'
            )
            == 't_123'
        )
        assert (
            parse_gdrive_confirm_token('<input type="hidden" name="confirm" value="t_456">')
            == 't_456'
        )


class OneDriveParserTest(TestCase):
    def test_build_onedrive_share_id_uses_unpadded_base64url(self) -> None:
        assert (
            build_onedrive_share_id('https://1drv.ms/u/s!abc') == 'aHR0cHM6Ly8xZHJ2Lm1zL3UvcyFhYmM'
        )

    def test_parse_onedrive_access_extracts_redirect_query(self) -> None:
        assert parse_onedrive_access(
            'https://onedrive.live.com/?cid=132bba39df79ee92'
            '&id=132BBA39DF79EE92!sc1b569d044ab4c9bbb6ad63b29e7298a'
            '&resid=132BBA39DF79EE92!sc1b569d044ab4c9bbb6ad63b29e7298a'
            '&redeem=aHR0cHM6Ly8xZHJ2Lm1zL2IvYy8xMzJi'
        ) == OneDriveAccess(
            cid='132bba39df79ee92',
            resid='132BBA39DF79EE92!sc1b569d044ab4c9bbb6ad63b29e7298a',
            redeem='aHR0cHM6Ly8xZHJ2Lm1zL2IvYy8xMzJi',
        )

    def test_parse_onedrive_link_extracts_download_url_name_and_size(self) -> None:
        assert parse_onedrive_link(
            {
                'name': 'sample.zip',
                'size': 1234,
                '@content.downloadUrl': 'https://public.dm.files.1drv.com/download',
            },
            'https://1drv.ms/u/s!abc',
        ) == DirectLink(
            name='sample.zip',
            url='https://public.dm.files.1drv.com/download',
            size='1234',
        )


class YandexDiskParserTest(TestCase):
    def test_parse_yandex_disk_link_extracts_download_url_name_and_size(self) -> None:
        assert parse_yandex_disk_link(
            {'name': 'sample.zip', 'size': 1234},
            {'href': 'https://downloader.disk.yandex.ru/disk/sample'},
            'https://disk.yandex.com/d/example',
        ) == DirectLink(
            name='sample.zip',
            url='https://downloader.disk.yandex.ru/disk/sample',
            size='1234',
        )


class DropboxParserTest(TestCase):
    def test_convert_dropbox_url_forces_direct_download(self) -> None:
        assert convert_dropbox_url(
            'https://www.dropbox.com/s/example/sample.zip?dl=0'
        ) == DirectLink(
            name='sample.zip',
            url='https://dl.dropboxusercontent.com/s/example/sample.zip?dl=1',
        )

    def test_convert_dropbox_url_rejects_other_domains(self) -> None:
        assert convert_dropbox_url('https://example.com/s/example/sample.zip?dl=0') is None


class FDroidParserTest(TestCase):
    def test_parse_fdroid_link_accepts_repo_apk_urls(self) -> None:
        assert parse_fdroid_link(
            'https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk'
        ) == DirectLink(
            name='org.fdroid.fdroid_1023052.apk',
            url='https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk',
        )

    def test_parse_fdroid_link_rejects_non_repo_urls(self) -> None:
        assert parse_fdroid_link('https://f-droid.org/packages/org.fdroid.fdroid/') is None
        assert parse_fdroid_link('https://example.com/repo/org.fdroid.fdroid_1023052.apk') is None

    def test_parse_fdroid_links_returns_first_package_apk_and_ignores_signatures(self) -> None:
        html = """
            <a href="https://f-droid.org/F-Droid.apk">Download F-Droid</a>
            <a href="https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk">
            <a href="https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk.asc">PGP Signature</a>
            <a href="/repo/org.fdroid.fdroid_1023051.apk">
        """

        assert parse_fdroid_links(html, 'https://f-droid.org/packages/org.fdroid.fdroid/') == [
            DirectLink(
                name='org.fdroid.fdroid_1023052.apk',
                url='https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk',
            )
        ]


class IzzyOnDroidParserTest(TestCase):
    def test_parse_izzyondroid_link_accepts_repo_apk_urls(self) -> None:
        assert parse_izzyondroid_link(
            'https://apt.izzysoft.de/fdroid/repo/com.aurora.store_75.apk'
        ) == DirectLink(
            name='com.aurora.store_75.apk',
            url='https://apt.izzysoft.de/fdroid/repo/com.aurora.store_75.apk',
        )

    def test_parse_izzyondroid_link_rejects_non_repo_urls(self) -> None:
        assert (
            parse_izzyondroid_link('https://apt.izzysoft.de/fdroid/index/apk/com.aurora.store')
            is None
        )
        assert (
            parse_izzyondroid_link('https://example.com/fdroid/repo/com.aurora.store_75.apk')
            is None
        )

    def test_parse_izzyondroid_links_returns_first_package_apk(self) -> None:
        html = """
            <a class='paddedlink' href='/fdroid/repo/com.aurora.store_75.apk'>Download</a>
            <a class='paddedlink' href='/fdroid/repo/com.aurora.store_73.apk'>Download</a>
        """

        assert parse_izzyondroid_links(
            html, 'https://apt.izzysoft.de/fdroid/index/apk/com.aurora.store'
        ) == [
            DirectLink(
                name='com.aurora.store_75.apk',
                url='https://apt.izzysoft.de/fdroid/repo/com.aurora.store_75.apk',
            )
        ]


class APKPureParserTest(TestCase):
    def test_parse_apkpure_app_id_extracts_package_from_app_pages(self) -> None:
        assert (
            parse_apkpure_app_id('https://apkpure.com/telegram/org.telegram.messenger')
            == 'org.telegram.messenger'
        )
        assert (
            parse_apkpure_app_id('https://m.apkpure.com/en/telegram/org.telegram.messenger')
            == 'org.telegram.messenger'
        )
        assert parse_apkpure_app_id('https://example.com/telegram/org.telegram.messenger') is None

    def test_parse_apkpure_links_returns_first_latest_version_asset(self) -> None:
        payload = {
            'version_list': [
                {
                    'title': 'Sample App',
                    'package_name': 'com.example.app',
                    'version_name': '1.2.3',
                    'version_code': '123',
                    'asset': {
                        'type': 'APK',
                        'url': 'https://download.cdnpure.com/b/APK/sample',
                        'size': '1234',
                    },
                },
                {
                    'title': 'Sample App',
                    'package_name': 'com.example.app',
                    'version_name': '1.2.2',
                    'version_code': '122',
                    'asset': {
                        'type': 'APK',
                        'url': 'https://download.cdnpure.com/b/APK/old',
                        'size': '1200',
                    },
                },
            ]
        }

        assert parse_apkpure_links(payload) == [
            DirectLink(
                name='Sample App_1.2.3_123.apk',
                url='https://download.cdnpure.com/b/APK/sample',
                size='1234',
            )
        ]

    def test_parse_apkpure_links_returns_empty_when_no_asset_url_exists(self) -> None:
        assert parse_apkpure_links({'version_list': [{'asset': {}}]}) == []


class AptoideParserTest(TestCase):
    def test_parse_aptoide_link_accepts_direct_apk_urls(self) -> None:
        url = 'https://pool.apk.aptoide.com/apps/com-example-app-1-123.apk'

        assert parse_aptoide_link(url) == DirectLink(name='com-example-app-1-123.apk', url=url)

    def test_parse_aptoide_link_rejects_non_apk_urls(self) -> None:
        assert parse_aptoide_link('https://mini-digital.en.aptoide.com/app') is None
        assert parse_aptoide_link('https://example.com/apps/com-example-app-1-123.apk') is None

    def test_parse_aptoide_app_id_extracts_next_payload_id(self) -> None:
        assert (
            parse_aptoide_app_id('<script>{"app":{"id":68027719,"name":"Sample"}}</script>')
            == '68027719'
        )
        assert parse_aptoide_app_id('<html></html>') == ''

    def test_parse_aptoide_links_extracts_direct_apk_from_api_payload(self) -> None:
        payload = {
            'nodes': {
                'meta': {
                    'data': {
                        'name': 'Sample App',
                        'package': 'com.example.app',
                        'size': 1234,
                        'file': {
                            'vername': '1.2.3',
                            'vercode': 123,
                            'filesize': 2345,
                            'path': 'https://pool.apk.aptoide.com/apps/com-example-app-123.apk',
                        },
                    }
                }
            }
        }

        assert parse_aptoide_links(payload) == [
            DirectLink(
                name='Sample App_1.2.3_123.apk',
                url='https://pool.apk.aptoide.com/apps/com-example-app-123.apk',
                size='2345',
            )
        ]

    def test_parse_aptoide_links_returns_empty_without_download_path(self) -> None:
        assert parse_aptoide_links({'nodes': {'meta': {'data': {'file': {}}}}}) == []


class HuaweiAppGalleryParserTest(TestCase):
    def test_parse_huawei_appgallery_id_accepts_app_and_appdl_urls(self) -> None:
        assert parse_huawei_appgallery_id('https://appgallery.huawei.com/app/C27162') == 'C27162'
        assert parse_huawei_appgallery_id('https://appgallery.huawei.com/#/app/C27162') == 'C27162'
        assert (
            parse_huawei_appgallery_id('https://appgallery.cloud.huawei.com/appdl/C27162')
            == 'C27162'
        )

    def test_parse_huawei_appgallery_id_rejects_invalid_urls(self) -> None:
        assert parse_huawei_appgallery_id('https://appgallery.huawei.com/app/not-valid') == ''
        assert parse_huawei_appgallery_id('https://example.com/app/C27162') == ''

    def test_parse_huawei_appgallery_redirect_extracts_package_name_from_apk_location(self) -> None:
        assert parse_huawei_appgallery_redirect(
            'https://appdlc-dre.hispace.dbankcloud.com/dl/appdl/application/apk/ad/hash/'
            'com.huawei.appmarket.2605121737.apk?trackId=0'
        ) == DirectLink(
            name='com.huawei.appmarket.apk',
            url=(
                'https://appdlc-dre.hispace.dbankcloud.com/dl/appdl/application/apk/ad/hash/'
                'com.huawei.appmarket.2605121737.apk?trackId=0'
            ),
        )

    def test_parse_huawei_appgallery_redirect_rejects_non_apk_locations(self) -> None:
        assert parse_huawei_appgallery_redirect('https://appgallery.cloud.huawei.com') is None


class APKComboParserTest(TestCase):
    def test_parse_apkcombo_app_url_standardizes_app_pages(self) -> None:
        assert (
            parse_apkcombo_app_url(
                'https://apkcombo.com/telegram/org.telegram.messenger/download/apk'
            )
            == 'https://apkcombo.com/telegram/org.telegram.messenger'
        )
        assert parse_apkcombo_app_url('https://example.com/telegram/org.telegram.messenger') == ''

    def test_parse_apkcombo_download_url_unwraps_r2_assets(self) -> None:
        assert (
            parse_apkcombo_download_url(
                '/r2?u=https%3A%2F%2Fassets.example.com%2Forg.telegram.messenger%2F67502.apk',
                'https://apkcombo.com/telegram/org.telegram.messenger',
            )
            == 'https://assets.example.com/org.telegram.messenger/67502.apk'
        )

    def test_parse_apkcombo_download_url_rejects_non_assets(self) -> None:
        assert (
            parse_apkcombo_download_url(
                '/r2?u=https%3A%2F%2Fassets.example.com%2Ffile.txt',
                'https://apkcombo.com/app/id',
            )
            == ''
        )

    def test_parse_apkcombo_filename_prefers_content_disposition_filename(self) -> None:
        assert (
            parse_apkcombo_filename(
                'https://assets.example.com/path/file.apks?'
                'response-content-disposition=attachment%3B%20filename%3D%22Telegram_12.7.3_apkcombo.com.xapk%22'
            )
            == 'Telegram_12.7.3_apkcombo.com.xapk'
        )

    def test_parse_apkcombo_links_returns_unique_variant_assets(self) -> None:
        html = """
            <div id="variants-tab">
              <a class="variant" href="/r2?u=https%3A%2F%2Fassets.example.com%2Fapp%2F67501.apk">APK</a>
              <a class="variant" href="/r2?u=https%3A%2F%2Fassets.example.com%2Fapp%2F67501.apk">APK</a>
              <a class="variant" href="/r2?u=https%3A%2F%2Fassets.example.com%2Fapp%2F67502.apks">XAPK</a>
            </div>
        """

        assert parse_apkcombo_links(
            html, 'https://apkcombo.com/telegram/org.telegram.messenger'
        ) == [
            DirectLink(name='67501.apk', url='https://assets.example.com/app/67501.apk'),
            DirectLink(name='67502.apks', url='https://assets.example.com/app/67502.apks'),
        ]


class ArchiveDirectLinksTest(TestCase):
    def test_archive_file_download_url_is_rendered_as_direct_link(self) -> None:
        archive_file = ArchiveFile('sample book.pdf', 'original')

        assert DirectLink(
            name=archive_file.name,
            url=archive_file.download_url('example_item'),
        ) == DirectLink(
            name='sample book.pdf',
            url='https://archive.org/download/example_item/sample%20book.pdf',
        )
