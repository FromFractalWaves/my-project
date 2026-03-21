from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from phase1a.models import CompletedCall
from phase1a.packet_builder import PacketBuilder
from phase1a.wav_writer import WavWriter


class PacketAndWavTests(unittest.TestCase):
    def test_wav_writer_and_packet_builder(self) -> None:
        call = CompletedCall(
            tgid=41003,
            lane_id=0,
            frequency=854_612_500,
            source_radio_id="1612266",
            started_at=100.0,
            ended_at=104.25,
            sample_rate=8000,
            audio_bytes=b"\x00\x00\x01\x00" * 10,
            end_reason="release",
        )
        with tempfile.TemporaryDirectory() as td:
            writer = WavWriter(Path(td))
            packet_id = str(uuid.uuid4())
            path = writer.write(call, packet_id)
            self.assertTrue(path.exists())
            packet = PacketBuilder().build(call, path, packet_id)
            self.assertEqual(packet.packet_id, packet_id)
            self.assertEqual(packet.source["talkgroup_id"], 41003)
            self.assertEqual(packet.payload["sample_rate"], 8000)
            self.assertEqual(packet.payload["audio_path"], str(path))


if __name__ == "__main__":
    unittest.main()
