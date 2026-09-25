"""Low pulse for the film. 75 s, 48 kHz mono, peak at -20 dBFS.

A sub thump once a second over a quiet two-note drone, faded in and out.
No samples, no downloads: the whole track is this file.
"""
import wave
from pathlib import Path

import numpy as np

RATE = 48_000
SECONDS = 75.0
PEAK_DBFS = -20.0
BEAT = 1.0          # seconds between pulses
OUT = Path(__file__).resolve().parents[1] / "public" / "pulse.wav"


def main() -> None:
    n = int(RATE * SECONDS)
    t = np.arange(n) / RATE

    # thump: 52 Hz falling to 44 Hz, fast attack, short exponential tail
    track = np.zeros(n)
    k = np.arange(int(RATE * 0.9)) / RATE
    freq = 44 + 8 * np.exp(-k * 18)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    env = (1 - np.exp(-k * 400)) * np.exp(-k * 6.5)
    thump = np.sin(phase) * env
    for start in np.arange(0.0, SECONDS - 0.9, BEAT):
        i = int(start * RATE)
        accent = 1.0 if int(round(start)) % 4 == 0 else 0.7
        track[i:i + len(thump)] += accent * thump

    # drone: fifth on A1, slow swell, well under the thump
    drone = 0.18 * np.sin(2 * np.pi * 55.0 * t) + 0.10 * np.sin(2 * np.pi * 82.41 * t)
    drone *= 0.6 + 0.4 * np.sin(2 * np.pi * t / 16.0) ** 2
    track += drone

    # fade in 2 s, out 3 s
    fade = np.ones(n)
    fin, fout = int(RATE * 2), int(RATE * 3)
    fade[:fin] = np.linspace(0, 1, fin)
    fade[-fout:] = np.linspace(1, 0, fout)
    track *= fade

    track *= (10 ** (PEAK_DBFS / 20)) / np.max(np.abs(track))
    pcm = np.clip(track * 32767, -32768, 32767).astype("<i2")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    peak = 20 * np.log10(np.max(np.abs(pcm)) / 32767)
    print("{}  {:.1f} s  peak {:.1f} dBFS".format(OUT, SECONDS, peak))


if __name__ == "__main__":
    main()
