# Munai camera capture

Capture one RTSP frame per invocation and save a timestamped JPEG when it differs
sufficiently from the last saved image.

## Image experiments

For offline image processing, open this folder in VS Code and select **Dev
Containers: Reopen in Container** (requires Docker and the Dev Containers
extension). The separate Python 3.10 environment includes OpenCV and notebook
tooling; it does not change the Pi capture requirements below.

OpenCode **2.2.0** is also installed. Run `opencode` in the container terminal;
connect your provider there if needed. Host credentials are not copied into the
image. After changing container tooling, select **Dev Containers: Rebuild and
Reopen in Container**.

See [PLAN.md](PLAN.md) for the next-session handoff: first build a simple
empty/chicken/egg pre-sorter, then consider pretrained detectors and training.
Run all experiment Python commands inside the container. Do not load `.env` for
offline experiments. The workspace is mounted, so treat its secrets accordingly.

### Lightweight capture sorter

```bash
/opt/venv/bin/python sort_captures.py                 # preview, no image moves
/opt/venv/bin/python sort_captures.py --move          # fill each folder to 10
/opt/venv/bin/python sort_captures.py --move --target 20 --seed 7
```

The offline helper randomly examines timestamped JPEGs directly in `captures/`
and moves selected originals into `captures/empty/`, `captures/chicken/`, and
`captures/eggs/`. Existing category images count toward the target; full categories
are skipped. No images are copied, deleted, or overwritten. A CSV move manifest
is saved in `captures/sort-*.csv`; an exclusive lock prevents concurrent sorters.
Moves use Linux's atomic no-replace rename (same filesystem required).

Only OpenCV and NumPy are needed. Processing uses 640×360 grayscale local texture:
smooth foreground regions are joined to find large chicken blobs; smaller smooth,
round regions are egg candidates. Chicken takes precedence over eggs. Optional local camera exclusions can mask text, logos, and walls. Thresholds are camera-specific heuristics,
not reliable labels or egg counts, especially for obscured eggs, changed camera
positions, or unusual lighting. `--chicken-area` (default 4500 downsampled pixels)
and `--texture-threshold` (default 8 intensity units) allow tuning. Originals retain
their resolution and filenames. Exhausting the candidates before reaching every
target returns a nonzero status without undoing completed moves.

Review local selections before relying on them; calibration is not a held-out
accuracy benchmark. This move-based workflow supersedes the earlier
preserve-in-place proposal in `PLAN.md`.

## Requirements

- Linux / Raspberry Pi OS with Python 3.5.3 or newer.
- System FFmpeg (including the Pi's FFmpeg 3.2.18).
- Network access to the camera.

No pip or npm packages are needed. Python manages configuration and scheduling
constraints; FFmpeg connects over RTSP/TCP and decodes the image.

## Configuration

Create a local `.env` beside `capture.py`, using `.env.example` as a template.
If you already have a `.env`, edit it locally rather than overwriting it.

```dotenv
RTSP_URL=rtsp://USER:PASSWORD@CAMERA_IP:554/STREAM_PATH
CAPTURE_DIR=captures
CAPTURE_TIMEOUT_SECONDS=30
FFMPEG_PATH=/usr/bin/ffmpeg
SIMILARITY_PIXEL_THRESHOLD=15
SIMILARITY_MIN_CHANGED_PERCENT=0.1
```

- `RTSP_URL` is required. Percent-encode special characters in URL credentials.
- `CAPTURE_DIR` defaults to `captures`, relative to the configuration file.
- `CAPTURE_TIMEOUT_SECONDS` defaults to 30 and bounds the FFmpeg process.
- `FFMPEG_PATH` defaults to `/usr/bin/ffmpeg`. A bare executable name is resolved
  through PATH; a relative executable path is relative to the configuration file.
- `SIMILARITY_PIXEL_THRESHOLD` defaults to 15 (integer, 0–255).
- `SIMILARITY_MIN_CHANGED_PERCENT` defaults to 0.1 (0–100); 0 disables filtering.

The standard-library parser accepts one `KEY=value` per line, optional `export `
prefixes, blank lines, and full-line `#` comments. Matching single or double
quotes around an entire value are removed. Values are literal: no interpolation,
escape processing, inline comments, multiline values, or shell execution.
Existing process environment variables do not override the file.

Restrict access, from the project directory:

```bash
chmod 600 .env
```

The file is gitignored. Do not paste its contents into logs or support requests.
Raw FFmpeg diagnostics are never printed because they can expose camera
credentials. The script maps recognized failures to fixed, credential-free
summaries (for example, authentication rejected or connection refused), along
with FFmpeg's numeric exit status. Unrecognized errors receive a generic summary.

## Run

```bash
python3 /home/pi/munai/capture.py
```

Adjust the absolute project path to match your Pi. A different configuration
file can be selected with `--env /absolute/path/to/config`.

Images are stored as:

```text
captures/20000101T120000_000000Z.jpg
```

The UTC timestamp records the start of the capture attempt, before connecting to
the stream; it is not a camera-generated exposure timestamp. Keep the Pi's clock
synchronized. Connection and decoding may add a delay before the captured frame.

A successful capture is renamed atomically from a temporary file in the same
directory, after FFmpeg succeeds and JPEG boundary markers are checked. Failed
or timed-out captures return status 1 and remove the temporary image. A shared
lock in the output directory prevents overlapping invocations using that output
directory; a skipped overlapping run exits successfully. Leave `.capture.lock`
in place. A forced kill or power loss may leave a hidden `.capture-*.jpg` file,
but never promotes it to a finished image.

## Skip similar images

Enabled by default, with no additional packages. After capturing a JPEG, FFmpeg
decodes it and the most recent saved timestamped JPEG into 320×180 grayscale
images. Python compares the resulting 57,600 brightness values. A pixel counts
as changed when the absolute difference exceeds `SIMILARITY_PIXEL_THRESHOLD`
(15 by default on a 0–255 scale). The log reports the changed count and percentage.

With the default `SIMILARITY_MIN_CHANGED_PERCENT=0.1`, fewer than 0.1% changed
comparison pixels means the new JPEG is deleted and the invocation exits
successfully. Otherwise it is saved at its original resolution. The reference
is always the last **saved** image, so gradual changes can accumulate until a new
image is kept. Only the flat capture directory's timestamped JPEGs are considered.

The first image is always saved. If comparison fails or exceeds the remaining
capture timeout budget, the new image is saved with a generic warning instead
of being discarded. Comparison adds up to two local JPEG decoding processes;
their combined timeout budget is what remains after the camera capture.

These are approximate scene-change counts, not full-resolution RGB pixel counts.
Downsampling and grayscale can miss very small objects or color-only changes;
lighting changes can still trigger saves. Review actual egg appearances before
settling on thresholds. Lower either threshold to preserve smaller changes;
raise them to skip more images, or set `SIMILARITY_MIN_CHANGED_PERCENT=0` to keep
every capture.

## Cron

Edit your user's crontab:

```bash
crontab -e
```

For one capture per minute:

```cron
* * * * * /usr/bin/python3 /home/pi/munai/capture.py >> /home/pi/munai/capture.log 2>&1
```

Cron supplies scheduling; the script performs a single capture and exits. Logs
and images accumulate, so choose a retention policy as collection grows.

## Private data and camera calibration

All names in this documentation and the tests are synthetic examples. Real hen
names, reference images, observations, and model artifacts belong outside Git.
Do not publish private deployment data or old, unsanitized Git history.

The offline helpers optionally read `camera-profile.json` beside the scripts.
This ignored file contains `horizontal_bounds: [left, right]` and
`excluded_rectangles: [[x1, y1, x2, y2], ...]` as JSON. Coordinates refer to the
640x360 processing frame; rectangles exclude overlays/walls, never identities.
Without a profile the entire processing frame is considered. Keep a private
backup of your profile alongside your references. Changing masks requires local
recognition review; generic thresholds are heuristics, not an accuracy guarantee.
