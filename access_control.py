# -*- coding: utf-8 -*-
import logging
from config import ALLOWED_GROUP_ID, ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

try:
    from telegram.ext import DispatcherHandlerStop
except ImportError:
    try:
        from telegram.ext.dispatcher import DispatcherHandlerStop
    except ImportError:
        try:
            from telegram.ext import ApplicationHandlerStop as DispatcherHandlerStop
        except ImportError:
            class DispatcherHandlerStop(Exception):
                """Fallback if DispatcherHandlerStop is not present."""
                pass


def access_control(update, context):
    """
    Enforces access control by restricting bot usage to the configured
    allowed_group_id and allowed_user_ids.
    """
    chat = getattr(update, 'effective_chat', None)
    user = getattr(update, 'effective_user', None)

    # 1. Restrict group chats
    if chat and getattr(chat, 'type', None) in ('group', 'supergroup', 'channel'):
        if ALLOWED_GROUP_ID is not None and ALLOWED_GROUP_ID != 0:
            if chat.id != ALLOWED_GROUP_ID:
                logger.warning(
                    "Unauthorized chat access attempted in chat %s (%s). Leaving chat.",
                    chat.id, getattr(chat, 'title', '')
                )
                try:
                    context.bot.leave_chat(chat.id, timeout=10)
                except Exception:
                    pass
                raise DispatcherHandlerStop()

    # 2. Restrict users across all interactions (messages, commands, callbacks, inline queries)
    if ALLOWED_USER_IDS:
        # Allow updates where the effective user is the bot itself (e.g. pinned message service messages)
        bot_id = None
        try:
            bot = getattr(context, 'bot', None)
            raw_id = getattr(bot, 'id', None)
            if isinstance(raw_id, int):
                bot_id = raw_id
            elif isinstance(raw_id, str) and raw_id.isdigit():
                bot_id = int(raw_id)
        except Exception:
            pass

        if bot_id is None:
            try:
                from config import TOKEN
                if TOKEN and ':' in str(TOKEN):
                    prefix = str(TOKEN).split(':', 1)[0]
                    if prefix.isdigit():
                        bot_id = int(prefix)
            except Exception:
                pass

        if user and bot_id is not None and user.id == bot_id:
            return

        if user is None or user.id not in ALLOWED_USER_IDS:
            if user:
                logger.info(
                    "Unauthorized user access attempted by user %s (%s). Dropping update.",
                    user.id, getattr(user, 'username', '')
                )
            raise DispatcherHandlerStop()
