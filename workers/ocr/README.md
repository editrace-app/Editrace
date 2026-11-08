# OCR worker (#13)

Finds on-screen text and returns it as `text_overlays` on one `text` track: the text, a normalized bbox, in/out frames
and style hints (colour, background box, alignment, size). PaddleOCR PP-OCRv5 mobile models, on the shared worker base.

```sh
python -m workers.run workers.ocr.worker:OcrWorker clip.mp4 --source source.json
```

## How it works

1. **Coarse pass.** OCR every 0.25 s at 720p. Readings are linked across samples into tracks (box overlap, or similar
   text with touching boxes). One empty sample doesn't split a track; one-sample readings are dropped as noise.
2. **Edge pass.** Each track's first and last frame is found by binary search between the samples, OCR-ing only a
   crop around the box (about 3 probes per edge). This is what gets the in/out times to the exact frame instead of
   to the nearest sample.
3. **Style.** Colour and background box come from the frame at the middle of the track. Font family and weight are
   not guessed (the schema fields are optional).

OCR on one keyframe per shot misses text that changes inside a shot, so the coarse pass is dense.

## What it does not do yet

- **Captions (#12).** Burned-in captions are read as overlays too. `detect.drop_caption_duplicates` removes them given
  the caption words; it runs where the two partials are merged, because a worker can't see another worker's output.
- **Scene text.** Signs and shop fronts in the footage are read like titles. Overlays are mostly stationary, and scene
  text moves with the camera, so filtering on box movement is the obvious next step; it needs labels to tune (below).
- **Accuracy numbers.** The golden labels (v01-v15) hold cuts and transitions only. The ±3 frame criterion needs text
  overlay labels, tracked in #52.

## Running it

The OCR worker has its own `requirements.txt` instead of an extra in `pyproject.toml`: paddleocr needs numpy below 2.4
and the cut worker's extra needs 2.4 or newer, so they can't share the root lockfile. CI therefore doesn't install
Paddle, and `test_paddle.py` skips there. Set up its own environment, and for a GPU install the GPU build of Paddle over
the CPU one (same version, same output, only faster):

```sh
uv venv && uv pip install -r workers/ocr/requirements.txt pydantic pytest
uv pip install paddlepaddle-gpu==3.3.1   --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/   --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match
```

Needs an NVIDIA driver that supports CUDA 12.6 or newer. Measured on an RTX 3060 Ti: 34 s of video in about 37 s,
model load included. Models download to `~/.paddlex` on first use.

`test_paddle.py` runs the real engine (skipped when `paddleocr` isn't installed); `test_detect.py` runs everything else
against a fake reader.
