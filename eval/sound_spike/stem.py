"""Spike #357: remove vocals with Demucs (htdemucs). WAV bytes on stdin -> no-vocals mono WAV on stdout.

Runs in a separate Python that has demucs + torch. Audio stays in memory: nothing is written to disk.
Timing goes to stderr.
"""

import io
import sys
import time
import wave

import numpy as np
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

t0 = time.perf_counter()
with wave.open(io.BytesIO(sys.stdin.buffer.read())) as w:
    rate = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
model = get_model("htdemucs")
model.eval()
assert rate == model.samplerate, rate
wav = torch.from_numpy(x)[None].repeat(model.audio_channels, 1)
mean, std = wav.mean(), wav.std() + 1e-8
with torch.no_grad():
    sources = apply_model(model, ((wav - mean) / std)[None], device="cpu", split=True, overlap=0.25)[0] * std + mean
music = sum(sources[i] for i, name in enumerate(model.sources) if name != "vocals").mean(0)
pcm = (music.clamp(-1, 1).numpy() * 32767).astype(np.int16)
out = io.BytesIO()
with wave.open(out, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(pcm.tobytes())
sys.stdout.buffer.write(out.getvalue())
print(f"stem {len(x) / rate:.1f}s audio in {time.perf_counter() - t0:.1f}s", file=sys.stderr)
