"""Compatibility import for the v2 session adapter.

The application uses :mod:`ole5_chat.agent.session` directly. This module
keeps existing integrations importing ``ole5_chat.sessions`` working while the
old graph is removed.
"""

from ole5_chat.agent.session import (
    AttachmentNotFound,
    SessionNotFound,
    attach,
    current_identity,
    detach,
    get,
    send,
    start,
    submit,
    progress,
    progress_stream,
)


