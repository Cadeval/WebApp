"""Django connection lifetimes around native asynchronous Bolt requests."""
from asgiref.sync import sync_to_async
from django.db import close_old_connections


class DatabaseConnectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    async def __call__(self, request):
        # ORM handlers use this same thread-sensitive executor. Calling Django
        # directly from the event loop would close a different connection.
        await sync_to_async(close_old_connections, thread_sensitive=True)()
        try:
            return await self.get_response(request)
        finally:
            await sync_to_async(close_old_connections, thread_sensitive=True)()
