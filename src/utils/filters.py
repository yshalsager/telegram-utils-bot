import regex as re
from telethon.events import NewMessage
from telethon.tl.custom import Message
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeCustomEmoji,
    DocumentAttributeImageSize,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)

from src import BOT_ADMINS
from src.utils.patterns import HTTP_URL_PATTERN


def is_admin_in_private(event: NewMessage.Event, _: Message) -> bool:
    return bool(event.is_private and event.sender_id in BOT_ADMINS)


def is_owner_in_private(event: NewMessage.Event, _: Message) -> bool:
    return bool(event.is_private and event.sender_id == BOT_ADMINS[0])


def has_file(event: NewMessage.Event, reply_message: Message | None) -> bool:
    return bool(
        (event.message.is_reply and reply_message and reply_message.file) or event.message.file
    )


def has_no_file(event: NewMessage.Event, reply_message: Message | None) -> bool:
    return not has_file(event, reply_message)


def is_reply_in_private(event: NewMessage.Event, _: Message | None) -> bool:
    return bool(event.is_private and event.message.is_reply)


all_media_types = ['audio', 'voice', 'video', 'video_note']


def has_media(event: NewMessage.Event, reply_message: Message | None, **media_types: bool) -> bool:
    """
    Check if the message or its reply contains specific types of media.

    This function is used to validate media conditions for various commands.

    :param event: The NewMessage event.
    :param reply_message: The message being replied to, if any.
    :param media_types: Keyword arguments specifying media type conditions.
        Supported media types and their meanings:
        - audio: True if audio file is required
        - voice: True if voice note is required
        - video: True if video file is required
        - video_note: True if video note is required
        - any: True if any of audio, voice, video, or video_note is required
        - not_audio: True if audio file should NOT be present
        - not_voice: True if voice note should NOT be present
        - not_video: True if video file should NOT be present
        - not_video_note: True if video note should NOT be present
        - audio_or_voice: True if either audio file or voice note is required
        - video_or_video_note: True if either video file or video note is required

    :return: True if the specified media conditions are met, False otherwise.

    Usage examples:
    - For audio commands: audio=True
    - For video commands: video=True or video_or_video_note=True
    - For voice note conversion: not_voice=True
    - For any media type: any=True
    """
    if not media_types:
        return True
    message = reply_message or event.message
    if not message.file:
        return False

    def check_media(_media_type: str) -> bool:
        return bool(getattr(message, _media_type, None))

    checks = []
    for media_type, should_have in media_types.items():
        if media_type == 'any':
            checks.append(any(check_media(t) for t in all_media_types) == should_have)
        elif media_type.startswith('not_'):
            actual_type = media_type[4:]
            other_types = [t for t in all_media_types if t != actual_type]
            checks.append(
                (not check_media(actual_type))
                and any(check_media(t) for t in other_types) == should_have
            )
        elif '_or_' in media_type:
            types = media_type.split('_or_')
            checks.append(any(check_media(t) for t in types) == should_have)
        else:
            checks.append(check_media(media_type) == should_have)

    return all(checks)


def is_file(event: NewMessage.Event, reply_message: Message | None) -> bool:
    """
    Check if the message or its reply contains an attachment uploaded as a file.
    :param event: The NewMessage event.
    :param reply_message: The message being replied to, if any.
    :return: True if the message or its reply contains an attachment uploaded as a file, False otherwise.
    """
    message = reply_message or event.message
    if not hasattr(message, 'document') or not hasattr(message.document, 'attributes'):
        return False
    for attribute in message.document.attributes:
        if isinstance(
            attribute,
            DocumentAttributeAnimated
            | DocumentAttributeAudio
            | DocumentAttributeCustomEmoji
            | DocumentAttributeImageSize
            | DocumentAttributeSticker
            | DocumentAttributeVideo,
        ):
            return False
    return True


def has_valid_url(
    event: NewMessage.Event, reply_message: Message | None, pattern: str = HTTP_URL_PATTERN
) -> bool:
    message = reply_message or event.message
    return bool(re.search(pattern, message.raw_text))


def has_file_with_ext(
    event: NewMessage.Event, reply_message: Message | None, ext: str | None = None
) -> bool:
    """
    Check if the message or its reply contains a file with a specific extension.

    :param event: The NewMessage event.
    :param reply_message: The message being replied to, if any.
    :param ext: The specific file extension to check for. If None, checks for any file.
    :return: True if the message or its reply contains a file matching the criteria, False otherwise.
    """
    message = reply_message or event.message
    if not message.file:
        return False
    if ext:
        return bool(message.file.ext == ext)
    return True


def has_pdf_file(event: NewMessage.Event, reply_message: Message | None) -> bool:
    return has_file_with_ext(event, reply_message, ext='.pdf')


def has_photo_or_photo_file(event: NewMessage.Event, reply_message: Message | None) -> bool:
    message = reply_message or event.message
    return bool(message.photo or (message.file and message.file.mime_type.startswith('image/')))
