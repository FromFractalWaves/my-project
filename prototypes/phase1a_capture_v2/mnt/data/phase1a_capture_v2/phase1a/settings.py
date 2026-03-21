from __future__ import annotations

from pathlib import Path

CONTROL_FREQ_HZ = 854_612_500
SOURCE_SAMPLE_RATE = 2_048_000
CHANNEL_DECIM = 32
CHANNEL_RATE = SOURCE_SAMPLE_RATE // CHANNEL_DECIM
SYMBOL_RATE = 4_800
AUDIO_RATE = 8_000
P25_CHANNEL_SPACING = 12_500
RTL_GAIN = 30
VISIBLE_BW_HZ = SOURCE_SAMPLE_RATE
VOICE_LANE_CAP = 8

META_ENDPOINT = "tcp://127.0.0.1:5557"
PCM_BASE_PORT = 5560
BACKEND_PCM_ENDPOINT = "tcp://127.0.0.1:5580"
BACKEND_CTRL_ENDPOINT = "tcp://127.0.0.1:5581"

POLL_TIMEOUT_MS = 10
INACTIVITY_TIMEOUT_S = 1.5
PCM_SAMPLE_WIDTH_BYTES = 2
PCM_CHANNELS = 1

OUTPUT_ROOT = Path("./out")
WAV_OUTPUT_DIR = OUTPUT_ROOT / "wav"
PACKET_JSONL_PATH = OUTPUT_ROOT / "packets.jsonl"

GRANT_EVENT_TYPES = {
    "channel_grant",
    "channel_update",
    "update",
    "call_start",
}
RELEASE_EVENT_TYPES = {
    "call_end",
    "release",
    "channel_release",
}


def pcm_endpoint(lane_id: int) -> str:
    return f"tcp://127.0.0.1:{PCM_BASE_PORT + lane_id}"
