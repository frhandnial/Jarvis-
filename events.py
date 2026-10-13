"""Messages from the assistant thread to the on-screen interface.

Kinds: status (standby/listening/thinking/speaking/muted), user, jarvis, tool, error, show, quit.
When no interface is running, emit() does nothing.
"""
import queue

q = queue.Queue()
enabled = False


def emit(kind, data=None):
    if enabled:
        q.put((kind, data))
