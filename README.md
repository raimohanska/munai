# Munai camera capture

Capture one RTSP frame per invocation and save a timestamped JPEG when it differs
sufficiently from the last saved image.

## Home Assistant MQTT chicken device

`chicken_device.py` is the combined entry point: capture one fresh camera image,
recognize the hen, optionally save the image, and update an MQTT sensor. Run it
once per minute with cron, **instead of** running `capture.py` separately.
It uses the same FFmpeg capture and reference-based recognizer, not a neural
network. Reference images must remain available even when capture storage is off.

### Minimal installation

On Raspberry Pi OS / Debian, prefer OS-built OpenCV and NumPy packages rather
than compiling or installing modern wheels on the old Pi:

```bash
sudo apt-get update
sudo apt-get install ffmpeg python3-opencv python3-numpy python3-pip ca-certificates
python3 -m pip install --user -r /home/pi/munai/requirements-device.txt
```

Adjust `/home/pi/munai` to your installation path. The only additional pip
dependency is **paho-mqtt==1.5.1**, pinned for Python 3.5 compatibility. Do not
upgrade to paho-mqtt 1.6+ on that Python. OpenCV 3.x/4.x and NumPy are used only
for image processing; no Pillow, notebooks, Node.js, or ML runtime is needed.
On an old Python 3.5 OS, use that distribution's compatible packages; verify
`python3 -c 'import cv2, numpy, paho.mqtt.client'` succeeds. Legacy OS repositories
may need maintenance; this is not a claim of hardware-tested Pi deployment.

For container development, the existing image provides OpenCV/NumPy; install the
new dependency with `/opt/venv/bin/python -m pip install -r requirements-device.txt`
or rebuild the container.

### Runtime configuration

The program reads the local `.env` at runtime with the existing literal-value
parser. Keep the camera settings from the capture configuration below and add:

```dotenv
MQTT_HOST=mqtt.example.invalid
MQTT_PORT=1883
MQTT_USER=YOUR_MQTT_USER
MQTT_PASSWORD=YOUR_MQTT_PASSWORD
MQTT_TLS=false
```

Use your actual broker settings locally, never in version control. Omit user and
password for an anonymous broker. `MQTT_HOST` is a hostname/IP, not a URL.
`MQTT_TLS` accepts `true` or `false`; TLS verifies the broker certificate using
system CA trust. The default port is 1883 without TLS or 8883 with TLS. Without
TLS, MQTT credentials and messages are unencrypted; use only on a trusted LAN.

- With a nonempty `CAPTURE_DIR`, captures are saved using the existing timestamp,
  atomic-write, and similarity-filtering behavior, now routed into category
  folders (below). Relative paths are resolved against the `.env` directory.
- **Omit `CAPTURE_DIR` entirely to disable persistent capture storage.** A temporary
  JPEG is still needed for recognition and is removed after the round, including
  on failure. Setting `CAPTURE_DIR=` is a configuration error, not the off switch.
- Recognition runs on **every successful capture**, even if similarity filtering
  decides not to store it. Position is not an identity feature; eggs are ignored.
- This optional-storage behavior belongs to `chicken_device.py`. The standalone
  legacy `capture.py` still defaults to storing in `captures/`.

Saved images are categorized automatically, preserving UTC filenames:

```text
CAPTURE_DIR/
  empty/YYYYMMDDTHHMMSS_ffffffZ.jpg
  eggs/YYYYMMDDTHHMMSS_ffffffZ.jpg
  chicken/
    hen_a/unreviewed/YYYYMMDDTHHMMSS_ffffffZ.jpg
    hen_b/unreviewed/YYYYMMDDTHHMMSS_ffffffZ.jpg
    hen_c/unreviewed/YYYYMMDDTHHMMSS_ffffffZ.jpg
    unknown/YYYYMMDDTHHMMSS_ffffffZ.jpg
```

Chicken detection takes priority over eggs. Only frames without a detected hen
are checked for egg candidates, using the existing lightweight shape/texture
heuristics; this separates `empty` and `eggs` but is not a reliable egg count.
Disagreement between the presence heuristics is conservatively categorized as an
unknown chicken. Similarity filtering compares against the latest saved image
**in the destination category**, so each category has its own baseline.

Automatic named-chicken images go under `unreviewed/` rather than beside the
confirmed reference JPEGs. Reference loading is nonrecursive and always excludes
the reserved `unknown/` directory, preventing automatic predictions (including
unknown hens) from silently becoming identity references. After visual review,
move a correct named image up one level to promote it to a reference; for unknown
images, move them into the appropriate confirmed named folder after identifying
the hen. No existing images are reorganized automatically.

### Run and discover

Keep confirmed timestamped reference JPEGs in `captures/chicken/hen_a/`,
`captures/chicken/hen_b/`, and `captures/chicken/hen_c/`. You can keep these in
another directory with `--references /absolute/path/to/chicken`.

```bash
python3 /home/pi/munai/chicken_device.py
```

Enable the **MQTT integration** in Home Assistant and point it at the same broker,
with discovery enabled (standard prefix `homeassistant`). No HA YAML sensor
configuration is needed. The default MQTT device ID is `munai_nest`; set a stable,
different `--device-id` for additional nests. The broker must allow this client
to publish to `homeassistant/sensor/<device-id>/chicken/config` and
`munai/<device-id>/state`.

The discovered **Munai / Chicken** text sensor has these states:

- `Hen_a`, `Hen_b`, or `Hen_c` when recognized.
- An empty string when no chicken is detected (including images containing eggs).
- `Unknown chicken` for a detected hen whose identity is ambiguous or unfamiliar,
  rather than falsely reporting empty. This is a literal sensor value, distinct
  from Home Assistant's built-in `unknown` state before receiving observations.
- HA `unavailable` after 180 seconds without an update; configure
  `--expire-after SECONDS` to exceed your scheduling interval.

State messages are JSON, e.g. `{"chicken":"Hen_a"}` or `{"chicken":""}`;
discovery extracts the name. Discovery is retained and republished each round;
state messages use QoS 1 and are **not retained**, avoiding replay of a stale
observation as fresh after an HA restart. After a restart, HA receives a fresh
state on the next scheduled round. No persistent MQTT connection or daemon is
needed. Failed captures publish no new state; old state expires. A broker failure
returns nonzero but does not undo an already saved capture. Errors never print
broker credentials, camera URLs, or raw external diagnostics.

Example cron entry (replace the old capture-only entry):

```cron
* * * * * /usr/bin/python3 /home/pi/munai/chicken_device.py >> /home/pi/munai/chicken-device.log 2>&1
```

The runtime user needs write access to the project directory for a stable overlap
lock, the capture directory when enabled, and system temporary storage. Keep
`.chicken-device.lock` in place. Each capture is bounded by
`CAPTURE_TIMEOUT_SECONDS`; MQTT connection and each acknowledged publication have
a separate timeout (`--mqtt-timeout`, default 10 seconds). Concurrent combined
runs skip rather than overlap. SIGKILL or power loss may leave temporary files.

Offline checks (no configuration read, camera, or broker connection):

```bash
/opt/venv/bin/python chicken_device.py --help
/opt/venv/bin/python -m unittest discover -s tests
```

## Image experiments

For offline image processing, open this folder in VS Code and select **Dev
Containers: Reopen in Container** (requires Docker and the Dev Containers
extension). The separate Python 3.10 environment includes OpenCV and notebook
tooling; it does not change the Pi capture requirements below.

OpenCode **2.2.0** is also installed. Run `opencode` in the container terminal;
connect your provider there if needed. Host credentials are not copied into the
image. After changing container tooling, select **Dev Containers: Rebuild and
Reopen in Container**.

See [PLAN.md](PLAN.md) for the original experiment plan. The current offline
helpers use traditional image processing, without trained models.
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

### Chicken recognizer (no trained model)

Confirmed reference images belong in named folders, for example
`captures/chicken/hen_a/`, `captures/chicken/hen_b/`, and
`captures/chicken/hen_c/`. Only timestamped JPEGs directly inside each named
folder are used as references. Folder names become identity labels.
The reserved `unknown/` folder and nested `unreviewed/` folders are not references.

```bash
/opt/venv/bin/python recognize_chickens.py --output recognition/run-001
/opt/venv/bin/python -m unittest discover -s tests
```

The recognizer scans the unsorted JPEGs directly in `captures/`. It never moves,
copies, deletes, or modifies original captures and never accesses `.env` or the
camera. Each run requires a new output directory to preserve previous reports:

- `predictions.csv`: identity, nearest reference, distance, ambiguity margin,
  foreground area/box, and measured features for every unsorted image.
- `hen_a-candidates.jpg`, `hen_b-candidates.jpg`, etc.: annotated contact sheets
  of strongest accepted matches, spaced at least ten minutes apart. Corresponding
  `*-candidates.txt` files list original paths in sheet order. The default is up
  to 24 previews per identity; use `--examples` to change it.
- `references.json`: reference measurements, feature scales, and run settings.
- `label-check.csv`: checks on labeled empty/egg images and identity references
  with the image itself excluded. Identities with just one reference are marked
  untested. Adjacent frames are correlated: these checks are **not** a held-out
  accuracy benchmark.

The appearance features use grayscale rather than colour. A large connected
low-texture region indicates a hen; small foreground objects, including eggs,
are ignored. The body mask is filled and eroded to reduce straw contamination.
Measurements include brightness quartiles relative to visible background straw,
the fraction of dark pixels, local texture quartiles, and edge density. These
summarize appearance without embedding any real hen measurements or identities. Distances are compared with each identity's closest reference;
there is no neural network, training, or fitted classifier.

Identity comparisons deliberately ignore coordinates, nest zones, body box size,
and orientation. Any hen can occupy any position;
we do not require a reference for every hen/position combination or treat absent
examples as evidence. Local masks may exclude camera text/walls only. The pooled
appearance summaries reduce positional dependence, but are not a guarantee of
pose invariance: different visible feathers, cropping, and uneven illumination
can still change measurements and produce an `unknown` result.

An identity is accepted only within `--max-distance` (default 1.25) and with a
sufficient gap to the next identity (`--min-margin`, default 0.25). Otherwise it
is `unknown`. Scores are heuristic distances, **not probabilities**. `no_chicken`
means the foreground-area threshold was not reached, not a guarantee that the
nest is empty. A detected hen always takes priority; eggs are not counted.
`--chicken-area` and `--texture-threshold` have the same defaults as the sorter.

Review candidate sheets before manually promoting images to confirmed folders.
Do not blindly feed predictions back as labels. Partial hens, multiple hens,
pose changes, lighting shifts, or a moved camera can defeat these heuristics.
The code keeps Python 3.5-compatible syntax/APIs, but runs here in the container;
deployment on the Pi still needs compatible OpenCV/NumPy packages and timing
checks (the container's modern package versions do not support Python 3.5).

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
