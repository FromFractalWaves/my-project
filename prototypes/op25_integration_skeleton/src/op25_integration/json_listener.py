from __future__ import annotations

import json
import logging
from typing import Any

from .call_tracker import CallTracker
from .models import Op25Signal

logger = logging.getLogger(__name__)


class JSONListener:
    """Receives UDP JSON payloads from OP25 and normalizes them."""

    def __init__(self, call_tracker: CallTracker) -> None:
        self.call_tracker = call_tracker

    def handle_datagram(self, datagram: bytes) -> Op25Signal | None:
        try:
            payload = json.loads(datagram.decode("utf-8"))
            signal = self._normalize_payload(payload)
        except Exception:
            logger.exception("Malformed or unparseable OP25 JSON dropped")
            return None

        self.call_tracker.handle_signal(signal)
        return signal

    def _normalize_payload(self, payload: dict[str, Any]) -> Op25Signal:
        normalized = {
            "type": payload.get("type", "OP25_SIGNAL"),
            "timestamp": payload["timestamp"],
            "talkgroup_id": payload["talkgroup_id"],
            "source_id": payload.get("source_id"),
            "frequency": payload.get("frequency"),
            "encrypted": payload.get("encrypted", False),
            "raw": payload,
        }
        return Op25Signal.from_dict(normalized)
