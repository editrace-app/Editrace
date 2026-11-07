# Golden-set labeling guide

How to hand-label cuts and transitions in the golden videos (#4, #52). Settle edge cases here **before** labeling, not while looking at a video.

## Setup

- Label the **conformed** constant-frame-rate file (`eval/golden/videos/vNN.mp4`), never the original upload. Labels and detectors use the same file.
- Label **blind**, before running or looking at any detector output.
- Record roughly how long each video took in the manifest. That tells #52 what 30 videos will cost.

## What to label

Labels are schema templates (`schema/template.schema.json`) with one video track. Every hand label has `"confidence": 1` and `"provenance": {"detector": "manual", "version": "1"}`.

| Element | Field | Frame to record |
|---|---|---|
| Hard cut | `cuts[].frame` | The **first frame of the new shot** |
| Transition | `transitions[]` with `type` and `range` | `range.start` is the first frame that's visibly mid-transition. `duration` runs to the first clean frame of the new shot |

## Rules

1. **Hard cut:** the picture changes to a different shot from one frame to the next. Label it.
2. **Jump cut** (same framing, time skip): a cut. Label it.
3. **Flash frame** (1–3 frames of a solid white/black frame or a different shot inserted between two shots): label **two cuts**, one at the first flash frame and one at the first frame after it. Write `flash` in the video's `notes` so it can be scored with and without flashes.
4. **Brightness flash over continuous footage** (an exposure or glow effect on the same shot): **not a cut**.
5. **Dissolve / crossfade / fade to black or white:** **not a cut.** Label it as a `transition` (`dissolve`, `fade_black`, `fade_white`). `fade_black` covers every direction: fading out to black, fading in from black, or dipping through black between two shots (same for white). Label one transition over the whole fade, through the black frames. A fade in at the very start or out at the very end is labeled too; it doesn't split the shot.
6. **Whip transition** (two shots joined behind motion blur): label a `transition` of type `whip`, **and** a cut at the first frame where the new shot is the main content. The cut is still there; it's just hidden by blur.
7. **In-camera whip pan** inside one continuous shot: **not a cut** and not a transition.
8. **Other edited transitions** (wipe, push, slide, zoom, glitch): label a `transition` with that type, or `other` plus `type_detail`. Add a cut only if there's a single frame where the shot switches outright.
   **Stacked transitions** (e.g. a zoom during a fade): label **each** one separately, with its own type and its own range, since they may start and end on different frames. Overlapping transitions count as one change of shot.
9. **Text, stickers, captions or overlays changing over the same footage:** **not a cut.**
10. **Zooms, speed ramps, reframes inside one shot:** **not a cut.**
11. **Split screen / picture-in-picture appearing:** a cut only if the whole frame changes at once. Otherwise list it in `notes`.
   **Picture-in-picture over a background** (e.g. a talking head in a box over still images): the background is the shot. A background change is a cut. Changes inside the box (a new clip, a time skip, the box moving, resizing, appearing or disappearing) are **not**.
12. **Platform end card** (e.g. Instagram's logo card added when a reel is downloaded, with or without a black frame before it): **not part of the edit.** Label nothing from its first frame to the end, not even the black frame (it's not a flash), and add it to the video's `exclude` list in the manifest, `{"start": <first>, "duration": <frames>, "reason": "platform end card"}`, so `score.py` leaves it out of scoring on both sides. Also note it in `notes` (`platform end card: frames <first>-<last>`) for people reading the manifest.
   **Instagram's black frame or the creator's?** Use the watermark, the small Instagram logo and @handle that Instagram stamps on every frame of the creator's video, black frames included. Black frames **with** the watermark are the creator's ending: part of the edit, labeled as usual (a cut to black, or a `fade_black`). The single black frame **without** it, right before the card, is Instagram's and belongs to the end card. Not every download has that black frame, and downloads saved some other way may have no watermark or card at all.
   **Black frames at the start or end that aren't part of the edit** (no content and no watermark, e.g. a blank frame added by the download or the platform): label nothing on them and add them to `exclude`, `"reason": "black frame, not part of the edit"`, with a note. At the start, extend the range by one frame, to the first frame with content, so the switch from black isn't scored as a cut (`score.py` only skips frames inside the range). Black frames **with** the watermark, or a fade, are the creator's and are labeled as usual (rule 5, and the watermark check above).
13. **Unsure:** record your best guess and list the frame in the video's `ambiguous` list in the manifest. These get reviewed with a second labeler, and they're reported separately in the results.

## Workflow

Videos are ingested first (`uv run python eval/golden/ingest.py <originals>`). That writes `videos/vNN.mp4`, the manifest entry and an empty `labels/vNN.json`. Name an original after the id you've claimed (`v13.mp4` becomes v13) so two labelers working in parallel don't collide; any other name gets the next free number. Keep originals outside `videos/` (e.g. in `videos/originals/`), since ingesting writes there.

### Labeler (default)

`eval/golden/labeler.html` runs in Chrome straight from disk (double-click it). The video never leaves your computer.

1. **Set the fps first.** Type the video's `fps` from `manifest.json` in the **fps** box: most are 30, v08 and v12 are 24, v06 is 23.976. The labeler counts frames when the video loads, so set the fps **before** picking the video; to change it later, reload the page and load the video again. With the wrong fps, every marker lands on the wrong frame.
2. **Load the video.** Pick (or drop) `videos/vNN.mp4`, the conformed file, never the one in `originals/`. The status line should read e.g. `v09: 483 frames at 30 fps`, and the count must match `frames` in the manifest; if it doesn't, the fps is wrong. "Restored autosaved markers" means earlier work on this video is back.
3. **Find each change.** The **Previous frame** is on the left and the **Current frame** on the right: a cut is where they're different shots. Play with `Space` until you see a change, pause (it stops on the frame shown), then step back with `←` until the current frame is the **first frame of the new shot**.

   | Key | Does |
   |---|---|
   | `←` / `→` | one frame back / forward (`Shift` = 10) |
   | `Space` | play / pause |
   | `J` / `K` | previous / next marker |
   | click the timeline | jump there (ticks: orange = cut, pink = flash, green = transition) |

4. **Mark it.**

   | Key | Marks |
   |---|---|
   | `C` | a cut on this frame, the first frame of the new shot (rules 1–2, and the inside of a whip, rule 6) |
   | `F` | a flash cut (rule 3): only for a 1–3 frame blip. Press `F` on the first flash frame, then `C` on the first frame after it. A different shot that lasts longer is a plain `C` |
   | `T` … `T` | a transition (rules 5–8): choose the type in the sidebar (`other` needs a detail, e.g. `split screen in`), press `T` on the first frame that's visibly mid-transition and again on its last frame. `Esc` cancels one you started |
   | `A` | toggles ambiguous (`?`) on the marker here (rule 13) |
   | `⌫` | deletes the marker here |

   A whip gets both a `whip` transition over the blur and a `C` where the new shot takes over. For stacked transitions, do `T`…`T` for each, changing the type in between. Where markers overlap (a cut inside a whip), click the one you mean in the list first, or use its ✕ / ? buttons. To move a marker, delete it and mark the right frame; a second `C` on the same frame replaces the first.

   Leave unmarked: zooms, speed ramps and reframes inside one shot (rule 10), brightness flashes on the same shot (rule 4), text and stickers (rule 9), and panels filling in inside a split screen (rule 11, note it in `notes`). Mark nothing from the first frame of a platform end card, even if its logo only fades in over the last shot; write that frame down for `exclude` (rule 12).
5. **Check, then save.** Step through 2–3 cuts with `K`: the previous frame should be the old shot and the current frame the new one. Then press `S` (**Save markers**) to download `vNN.markers.json`. Markers autosave in the browser, but only the downloaded file counts. Chrome names a second save `vNN.markers (1).json`, so use the newest. Labeling time is counted while you press keys, ignoring gaps over 2 minutes, and saved with it.
6. **Convert:** `uv run python eval/golden/markers_to_label.py vNN ~/Downloads/vNN.markers.json`. This fills `labels/vNN.json` (cuts, transitions, and slots derived from them) and the manifest's `notes`, `ambiguous` and `labeling_minutes`, and refuses a file whose frame count doesn't match. Re-running replaces the previous labels and keeps `exclude`.
7. **Finish the manifest entry:** add the end card to `exclude` (rule 12), and fill in `rights`, `edit_styles` and `music` (see below).

To keep editing later: set the fps, load the same video, then **Resume** with your saved `vNN.markers.json`, which replaces whatever the browser autosaved.

### DaVinci Resolve (alternative)

Make a timeline at the video's fps starting at `01:00:00:00` and put **timeline markers** on it, named `cut` (or left at the default), `flash`, a transition type with a **duration** covering it, or `other: <what it is>`. Add `?` to any name for ambiguous. Export with File → Export → Timeline Markers to EDL, then run `markers_to_label.py vNN markers.edl`.

## Manifest fields

`manifest.json` is committed, so it never names or links to the people in the videos.

- **`rights`**: the usage rights for the video, e.g. `"full"` (any use permitted by the owner) or `"self-made"`.
- **`edit_styles`**: tags for the editing in the video, so results can be broken down by style. Use these; add a new tag only if none fit:

  | Tag | Use when |
  |---|---|
  | `fast-cuts` | lots of cuts, under ~1 s per shot |
  | `slow-paced` | few cuts, long shots |
  | `beat-synced` | cuts clearly land on the music |
  | `flash` | has flash cuts |
  | `whip` | has whip transitions |
  | `dissolve` | has dissolves or fades |
  | `jump-cuts` | same framing, skipping ahead in time |
  | `talking-head` | someone talking to camera |
  | `montage` | a sequence of different clips |
  | `text-heavy` | lots of on-screen text or captions |
  | `speed-ramp` | speeds up or slows down within shots |
  | `zoom` | zoom punches or zoom transitions |
  | `lip-sync` | someone mouthing along to trending audio (movie dialogue, a song), usually to camera |

- **`music`**: what's on the soundtrack, used by the sound-ID spike (#11):
  - `"released"`: a commercially released song
  - `"original"`: a platform original sound, a remix, or a sped-up/slowed edit
  - `"none"`: no music (speech, ambient sound or silence)
  - `"unknown"`: there's music, but you can't tell which kind

  Don't write the song's name: #11 checks whether the vendors find it on their own.

- **`held_out`**: `true` for the 10 test-set videos of milestone 3 (#52), which nobody tunes on: CI's golden eval skips them (`ci_eval.py`), and they're scored only at the end of Phase 1. Leave it out for every other video. Only a labeled video can be held out, and none of v01–v15 can be, since they've all been used for tuning.

## After the detectors run

Review every disagreement between labels and detectors. If a label was wrong, fix it, and report how many labels changed alongside the results. Never change a label silently to match a detector.
