from pathlib import Path

from capture_pipeline import CaptureConfig, CaptureRuntime
from capture_pipeline.models import MetadataEvent, PCMChunk


def test_explicit_call_end_emits_packet(tmp_path: Path) -> None:
    rt = CaptureRuntime(CaptureConfig(output_dir=tmp_path, inactivity_timeout_s=1.0))
    rt.handle_metadata_event(MetadataEvent(event_type="call_start", tgid=41003, source_radio_id="1612266", frequency=854612500))
    rt.handle_pcm_chunk(PCMChunk(tgid=41003, pcm=b"\x00\x00" * 100, sample_rate=8000))
    packets = rt.handle_metadata_event(MetadataEvent(event_type="call_end", tgid=41003))
    assert len(packets) == 1
    assert packets[0]["metadata"]["source_radio_id"] == "1612266"
