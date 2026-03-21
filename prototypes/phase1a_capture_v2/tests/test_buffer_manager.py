from __future__ import annotations

import unittest

from phase1a.buffer_manager import BufferManager
from phase1a.models import MetadataEvent, PCMHeader


class BufferManagerTests(unittest.TestCase):
    def test_grant_pcm_release_produces_completed_call(self) -> None:
        bm = BufferManager(sample_rate=8000, inactivity_timeout_s=1.0)

        completed = bm.handle_metadata(
            MetadataEvent(json_type="channel_grant", tgid=41003, freq=854_612_500, lane_id=0, srcaddr="1612266", ts=100.0)
        )
        self.assertEqual(completed, [])

        bm.handle_pcm(
            PCMHeader(lane_id=0, tgid=41003, freq=854_612_500, ts=100.1, source_radio_id="1612266"),
            b"\x01\x02\x03\x04",
        )

        completed = bm.handle_metadata(
            MetadataEvent(json_type="release", tgid=41003, lane_id=0, ts=101.0)
        )
        self.assertEqual(len(completed), 1)
        call = completed[0]
        self.assertEqual(call.tgid, 41003)
        self.assertEqual(call.lane_id, 0)
        self.assertEqual(call.source_radio_id, "1612266")
        self.assertEqual(call.audio_bytes, b"\x01\x02\x03\x04")

    def test_timeout_flushes_idle_call(self) -> None:
        bm = BufferManager(sample_rate=8000, inactivity_timeout_s=0.5)
        bm.handle_pcm(
            PCMHeader(lane_id=1, tgid=500, freq=855_000_000, ts=10.0, source_radio_id=None),
            b"\x00\x00",
        )
        completed = bm.flush_timeouts(now=10.6)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].end_reason, "inactivity_timeout")


if __name__ == "__main__":
    unittest.main()
