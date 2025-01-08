# log_handlers.py

import logging
import pprint

from webapp.settings import USER_LOGS

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


class InMemoryLogHandler(logging.Handler):
    """
    A logging handler that keeps a deque of the most recent log messages
    AND broadcasts each new log message to a Channels group.
    """

    def __init__(self, max_entries=1000):
        super().__init__()
        self.max_entries = max_entries
        self.records = USER_LOGS
        self.channel_layer = get_channel_layer()
        self.group_name = "log_stream"  # all logs go here

    def sync_emit(self, record: object, user_id: str = None) -> None:
        # Format the record into a string
        if isinstance(record, logging.LogRecord):
            msg = self.format(record)
        else:
            msg = pprint.pformat(object=record, width=120,
                                 sort_dicts=True,
                                 compact=True, )

        # Save it in memory
        # user_id = getattr(msg, 'user_id', None)

        if user_id is not None:
            # Append to the user's deque
            USER_LOGS[user_id].append(msg)
        else:
            # If no user_id, skip or store in a "global" or "anonymous" queue
            USER_LOGS["anonymous"].append(msg)

        # Broadcast this log to the "log_stream" group
        if self.channel_layer is not None:  # Ensure Channels is ready
            async_to_sync(self.channel_layer.group_send)(
                self.group_name,
                {"type": "new_log", "message": msg}
            )

    async def emit(self, record: object, user_id: str = None) -> None:
        # Format the record into a string
        if isinstance(record, logging.LogRecord):
            msg = self.format(record)
        else:
            msg = pprint.pformat(object=record, width=120,
                                 sort_dicts=True,
                                 compact=True, )
        # Save it in memory
        # user_id = getattr(msg, 'user_id', None)

        if user_id is not None:
            # Append to the user's deque
            USER_LOGS[user_id].append(msg)
        else:
            # If no user_id, skip or store in a "global" or "anonymous" queue
            USER_LOGS["anonymous"].append(msg)

        # Broadcast this log to the "log_stream" group
        if self.channel_layer is not None:  # Ensure Channels is ready
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "new_log", "message": msg}
            )

    async def get_logs(self, user_id):
        """
        Get a list of recent logs in chronological order.
        """
        return list(USER_LOGS[user_id])
