from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


EventHandler = Callable[[dict[str, Any]], None]


class EventBus:
    """Synchronous in-process pub/sub.

    Handlers run sequentially in the caller's thread. Exceptions are not isolated
    here because the architecture spec explicitly keeps execution semantics simple.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        for handler in self._subscribers.get(event_type, []):
            handler(data)
