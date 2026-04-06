from __future__ import annotations

from pathlib import Path

CONTROL_FREQ_HZ = 854_612_500
CENTER_FREQ_HZ = 855_750_000       # shifted to cover voice channels at 856-857 MHz
SOURCE_SAMPLE_RATE = 3_200_000      # 3.2 Msps — ±1.6 MHz visible band
CHANNEL_DECIM = 50                  # 3_200_000 / 50 = 64_000 sps (same channel rate)
CHANNEL_RATE = SOURCE_SAMPLE_RATE // CHANNEL_DECIM
CONTROL_OFFSET_HZ = CONTROL_FREQ_HZ - CENTER_FREQ_HZ  # -1_137_500 Hz
SYMBOL_RATE = 4_800
AUDIO_RATE = 8_000
P25_CHANNEL_SPACING = 12_500
RTL_GAIN = 30
VISIBLE_BW_HZ = SOURCE_SAMPLE_RATE
VISIBLE_MIN_HZ = CENTER_FREQ_HZ - VISIBLE_BW_HZ // 2  # 854_150_000
VISIBLE_MAX_HZ = CENTER_FREQ_HZ + VISIBLE_BW_HZ // 2  # 857_350_000
VOICE_LANE_CAP = 8

META_ENDPOINT = "tcp://127.0.0.1:5557"
PCM_BASE_PORT = 5560
BACKEND_PCM_ENDPOINT = "tcp://127.0.0.1:5580"
BACKEND_CTRL_ENDPOINT = "tcp://127.0.0.1:5581"

POLL_TIMEOUT_MS = 10
INACTIVITY_TIMEOUT_S = 1.5
LANE_STALE_TIMEOUT_S = 5.0          # release lane if tgid not seen in grant stream for this long
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
