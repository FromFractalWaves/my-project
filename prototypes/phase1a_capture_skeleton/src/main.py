from __future__ import annotations

import json
import time
from pathlib import Path

from capture_pipeline import CaptureConfig, CaptureRuntime
from capture_pipeline.models import MetadataEvent, PCMChunk


def demo() -> None:
    cfg = CaptureConfig(output_dir=Path("./recordings_demo"), inactivity_timeout_s=0.2)
    rt = CaptureRuntime(cfg)

    rt.handle_metadata_event(MetadataEvent(event_type="call_start", tgid=41003, source_radio_id="1612266", frequency=854612500))
    rt.handle_pcm_chunk(PCMChunk(tgid=41003, pcm=b"\x00\x00" * 800, sample_rate=8000))
    rt.handle_pcm_chunk(PCMChunk(tgid=41003, pcm=b"\x01\x00" * 800, sample_rate=8000))
    time.sleep(0.25)
    packets = rt.poll()
    print(json.dumps(packets, indent=2))


if __name__ == "__main__":
    demo()
