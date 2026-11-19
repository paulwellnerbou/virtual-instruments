"""Stop the server once no browser has been connected for a while.

Used when the launcher app starts the server: it runs only while the builder is open somewhere.
"""

import asyncio
import time

from nicegui import Client, app, background_tasks

CHECK_EVERY_S = 5.0


def pages_open() -> bool:
    # NiceGUI's own registry: counting connect/disconnect events ourselves drifts, because
    # NiceGUI doesn't always pair them (e.g. a socket that drops while it is being set up).
    return any(c.has_socket_connection for c in Client.instances.values())


class IdleExit:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.idle_since = time.monotonic()  # counts from start, in case no browser ever connects

    def should_exit(self, now: float, open_pages: bool) -> bool:
        if open_pages:
            self.idle_since = now
            return False
        return now - self.idle_since >= self.seconds

    async def watch(self) -> None:
        while not self.should_exit(time.monotonic(), pages_open()):
            await asyncio.sleep(CHECK_EVERY_S)
        app.shutdown()


def enable(seconds: float) -> None:
    watcher = IdleExit(seconds)
    app.on_startup(lambda: background_tasks.create(watcher.watch()))
