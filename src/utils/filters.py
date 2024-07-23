from telethon.events import NewMessage
from telethon.tl.custom import Message

from src import BOT_ADMINS
from src.utils.reply import ReplyState, reply_states


def is_admin_in_private(event: NewMessage.Event, _: Message) -> bool:
    return bool(event.is_private and event.sender_id in BOT_ADMINS)


def is_owner_in_private(event: NewMessage.Event, _: Message) -> bool:
    return bool(event.is_private and event.sender_id == BOT_ADMINS[0])


def has_file_or_reply_with_file(event: NewMessage.Event, reply_message: Message | None) -> bool:
    return bool(
        (event.message.is_reply and reply_message and reply_message.file) or event.message.file
    )


def is_reply_in_private(event: NewMessage.Event, _: Message | None) -> bool:
    return bool(event.is_private and event.message.is_reply)


def has_media_or_reply_with_media(
    event: NewMessage.Event, reply_message: Message | None, **media_types: bool
) -> bool:
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

    def check_media(_media_type: str) -> bool:
        return bool(getattr(message, _media_type, None))

    checks = []
    for media_type, should_have in media_types.items():
        if media_type == 'any':
            checks.append(
                any(check_media(t) for t in ['audio', 'voice', 'video', 'video_note'])
                == should_have
            )
        elif media_type.startswith('not_'):
            actual_type = media_type[4:]
            checks.append(check_media(actual_type) != should_have)
        elif '_or_' in media_type:
            types = media_type.split('_or_')
            checks.append(any(check_media(t) for t in types) == should_have)
        else:
            checks.append(check_media(media_type) == should_have)

    return all(checks)


def is_valid_reply_state(event: NewMessage.Event) -> bool:
    return (
        event.is_private
        and event.is_reply
        and event.sender_id in reply_states
        and reply_states[event.sender_id]['state'] == ReplyState.WAITING
        and event.message.reply_to_msg_id == reply_states[event.sender_id]['reply_message_id']
    )
