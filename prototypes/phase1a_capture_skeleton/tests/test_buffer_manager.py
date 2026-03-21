from pathlib import Path
import time

from capture_pipeline.buffer_manager import BufferManager
from capture_pipeline.models import MetadataEvent, PCMChunk


def test_timeout_flush_creates_packet(tmp_path: Path) -> None:
    mgr = BufferManager(output_dir=tmp_path, inactivity_timeout_s=0.01)
    mgr.handle_metadata(MetadataEvent(event_type="call_start", tgid=123, source_radio_id="abc", frequency=100))
    mgr.handle_pcm(PCMChunk(tgid=123, pcm=b"\x00\x00" * 200, sample_rate=8000))
    time.sleep(0.02)
    emissions = mgr.poll_timeouts()
    assert len(emissions) == 1
    assert emissions[0].packet.source["talkgroup_id"] == 123
    assert emissions[0].wav_path.exists()
