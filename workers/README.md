# Workers

Python analysis pipeline running on Modal (serverless GPU).

## Stack

- **Runtime:** Modal (serverless GPU)
- **Language:** Python 3.11+
- **ML frameworks:** chosen per worker (PyTorch, librosa and OpenCV are the likely ones)
- **Orchestration:** the orchestrator (#59) dispatches jobs from the jobs table (#58). Neither exists yet.

## The worker base (#53)

Every worker subclasses `workers.base.Worker` and implements `detect()`. The base does the rest: it looks the job up in the
result store, downloads the video and checks it against the job's content hash, runs `detect()`, stamps provenance on
every element, validates the partial template against the strict schema models, stores it and reports status.

```python
class CutsWorker(Worker):
    name = "cuts"  # the provenance detector
    version = "0.1.0"  # bump on any change that can change the output
    compute = Compute(gpu="T4", memory_mb=8192)

    def params(self):
        return {"threshold": 0.5}  # anything that changes the output

    def detect(self, video: Path, job: Job) -> Partial:
        return {"tracks": [...]}  # tracks, beats and sound_ref only
```

- **Cache.** The key is content hash, worker, version and a hash of `params()`. Re-running a job with the same inputs returns the stored result without fetching the video or calling `detect()`. Only successful runs are stored. Change `version` or a param and it is a miss.
- **Retries.** `TransientError`, timeouts, connection errors and 5xx/429 responses are retried with capped, jittered exponential backoff. Bugs, bad input and a missing file fail at once, because a retry on a GPU container is billed.
- **Fetching.** Workers get a short-lived signed https URL, never storage credentials, and the download must hash to the claimed `content_hash`.
- **Status.** Each run emits `running`, then `succeeded` (with `cached` and `duration_s`) or `failed`, as a JSON log line carrying `job_id`.
- **Modal.** The base doesn't import Modal. `compute` is data for the Modal wrapper that goes in with the first deployed worker (#7).

Try it without Modal or a database:

```sh
python -m workers.run workers.reference:ReferenceWorker clip.mp4 --source source.json
```

A second run prints `cache hit`. `workers/reference.py` is the smallest possible worker.

## Writing a worker

Subclass `Worker` as above. One worker per detector, each in its own module under `workers/`. `workers/reference.py` is the template to copy. The planned detectors are tracked as issues, not here.

## Output

All workers write results to `schema`-valid template JSON format.

## Generated Code

Pydantic models are **generated from `schema/template.schema.json`**. Do not hand-write models — regenerate from schema.

## Beat/downbeat worker (#14)

`workers.beats.worker:BeatsWorker` uses [beat_this](https://github.com/CPJKU/beat_this)
1.1.0, `final0`, CPU float32, without DBN postprocessing. Install with `uv sync
--extra beats`. FFmpeg must be on PATH, or set `EDITRACE_FFMPEG` to its executable.
Provision the official `final0` checkpoint in the worker image at
`/opt/models/beat_this-final0.ckpt`, or set `EDITRACE_BEATS_CHECKPOINT` locally.
The worker verifies SHA-256
`8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331`
before loading; it never downloads weights during a job. The upstream download is
`https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt`.

```sh
python -m workers.run workers.beats.worker:BeatsWorker clip.mp4 --source source.json
```

Audio is decoded to a mono 22,050 Hz float32 pipe and stays in memory. FFmpeg
preserves initial audio delay on the source timeline. The worker rejects sources
longer than 180 seconds and bounds decoding to 181 seconds with a 90-second
subprocess timeout. The owned sample buffer is cleared in `finally`, including
inference failures; temporary source-video cleanup belongs to the base. Process
memory is not a secure-erasure guarantee: inference libraries can hold temporary
copies, but no PCM is passed to storage, return values or logs.

Output is the optional `beats` section: median-interbeat BPM, sorted unique
integer-frame beats/downbeats using rational source fps and half-up rounding.
Downbeats are included in beats, and markers outside the source are discarded.
Silence, no audio, fewer than three markers or tempo outside the schema produce
an empty partial. Decode/model errors use the base's structured failed status;
the orchestrator must preserve other workers' output on partial failure (#59).
Confidence is a provisional grid-regularity heuristic, capped at 0.9. It is not a
music-presence classifier or a calibrated probability: speech/noise can generate
a grid, and half/double-tempo mistakes remain possible. No automatic cut snapping.

The cache includes model checksum, decoding/detection settings and library/FFmpeg
versions. Logs carry job ID, elapsed time, process CPU seconds and zero GPU
seconds. Modal resource and cost measurements remain pending (#320); CPU seconds
are process time, not billed wall-clock core seconds.

There are no hand beat/downbeat labels in #52 yet, so golden-set P/R is **not
evaluable**. Unit/integration tests cover frame conversion, schema validation,
cache reuse, failures and non-persistence. Real-model runs require provisioned
weights and are recorded on the PR; the fast suite never downloads a model.

This worker never emits `sound_ref`. ACRCloud suggestions in proposed ADR 0006
(PR #306) require private candidate persistence and owner confirmation outside the
partial-template cache; production use remains gated on #21/#56. End-to-end editor
marker imports still depend on #9/#10, so this implementation alone does not
complete #14.
