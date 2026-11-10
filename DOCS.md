# Munai: install, configure, and transfer references

For the step-by-step setup and what to do next, start with [README.md](README.md).
This file covers advanced settings, storage, offline tooling, and validation details.

Munai is an **experimental Home Assistant app**. It builds locally on the Green,
like Paine; no container registry is required. The repository root is the app's
build context. Supervisor discovers the root `config.json` and `repository.json`.
Docker's explicit allowlist excludes Git history, `.env`, references, and local
camera calibration. The legacy Pi/cron entry points remain available unchanged
in usage; do not retire them until the HA pilot works.

## Status page (0.1.12+)

**Open Web UI** opens Status by default. The **MunAI** sidebar links **Status**,
**Journal**, **Captures**, **References** and **Settings**. Status refreshes every 15 seconds and shows the latest
successfully processed image with its timestamp, age and image-specific prediction,
separately from the confirmed state at that capture. Confirmation may lag the raw
prediction. Unknown/obscured means uncertainty, not definite hen presence. Expand
recognition diagnostics to inspect raw per-zone predictions and available scores.

Capture enabled/disabled/failed state, MQTT connectivity and current sanitized errors
appear above the image. A stale warning appears once the image age exceeds the
larger of `expire_after` and twice `capture_interval`.
This is not a live stream or a new on-demand camera request. Failed recognition
keeps the previous successful observation visible with its original timestamp.

From 0.1.31, all UI times and day boundaries use Home Assistant's local timezone.
Stored filenames and result timestamps stay UTC. The latest-event display and
MQTT sensor retain the latest event across nights and app restarts, showing
**No events yet** only when no event exists.

At most two full images (8 MiB each maximum) are held in memory, independent of
`store_captures`, photo deduplication and journal cleanup. Timestamp-pinned URLs
never substitute a newer photo; expired URLs return unavailable. After an app
restart, Status waits for a new successful observation rather than showing an
unpaired old image. Access remains through HA Ingress, not a public image URL.

## Event journal

From the Munai app page in HA, choose **Open Web UI**, then **Journal**. Enable **Show in sidebar**
for a persistent MunAI link. HA Ingress authenticates access; there is no new
host port or journal password. The existing HTTPS transfer API remains separate.

Entries appear newest first, grouped by Home Assistant local day, with local
times in their headings. Each card shows one representative full-nest photo;
the actual contributing photos remain in **Details & pictures**. Click a photo
for the original; pages contain at
most 20 events and the latest page refreshes every minute. A confirmation time is
not an exact laying time. Identity/count predictions remain experimental and
are not human-reviewed labels.

Arrival decisions retain their three confirming frames; departures retain five.
Laying entries
also retain the before-visit egg baseline and identity evidence; delayed egg
confirmation retains the departure evidence too, including across restarts.
Collection entries show the previous count and the three zero-count frames.
Pre-upgrade evidence cannot be reconstructed; existing visit state is preserved
but incomplete journal evidence is identified on the page.

`journal_retention_days` defaults to **365** (range 1–3650). This is independent
of `retention_days`, `capture_storage_mb`, and `store_captures`: you can leave
ordinary capture storage off and still get the journal. Full JPEG copies live in
`/data/journal/images/`, event JSON in `/data/journal/entries/`; active decision
evidence and an event outbox are saved atomically with `/data/nest-state.json`.
The same journal-owned image may support multiple entries. These files are never
loaded as recognition references or exposed through public HA `/local/` URLs.

Hourly cleanup removes expired entries, then unreferenced photos. A bounded set
of active baseline/visit evidence can outlive the configured retention until that
decision is resolved. Superseded candidate frames are discarded after successful
rounds. The page shows archive usage at the last cleanup and warns about low disk
space. The journal has no aggressive size cap: if the disk reserve is reached,
new observations stop rather than silently deleting younger entries. Reduce
retention or free space before that happens. As a rough estimate, 20 events/day ×
6 unique photos × 200 KB is about 8.8 GB/year; resolution and shared photos change
this substantially. Include Munai app data in HA backups; a one-year retention
setting is not itself a backup. Journal data is private and must not be committed.

The journal stores new events only; it does not backfill the old latest-event
sensor or reinterpret historical captures. No MQTT, recognition thresholds or
appearance-based identity rules change.
Back up app data before upgrading: older releases cannot read the new nest-state
format, so a downgrade also requires restoring the matching pre-upgrade backup.

## Settings: reset state (0.1.47+)

Open **Settings** in the MunAI Web UI, choose **Reset state**, and confirm. This
zeros today's production totals for every identity, including Unknown and split
carry, and clears current occupants, egg locations/counts, pending attribution,
confirmation streaks and the latest-event display. It does **not** correct a
total to a chosen nonzero value or reconstruct earlier laying events.

References, captures, results, training/reviews, journal entries/images, app
configuration and credentials remain unchanged. Existing MQTT/InfluxDB history
is not rewritten. The app immediately republishes the zero daily snapshot and
unknown live state; an MQTT outage is reported as an update pending, with daily
totals retried by the scheduler. Capture continues if enabled. The first clear
observations establish fresh egg baselines without counting existing eggs again;
that baseline-pending state survives restarts. Subsequent new eggs count normally.

Reset is available only through HA Ingress, requires a page-specific authorization
token and explicit confirmation, and rejects an action from a previous local day.
If capture processing is busy, try again shortly. A durable reset intent is replayed
before nest loading after an interrupted reset; old split transitions cannot
restore the cleared counts. Back up the app before upgrading or resetting state.

## Stateful egg tracking (0.1.46+)

The frame detector still reports only eggs with a sufficiently complete smooth,
curved outline. A separate egg-only tracker remembers each accepted egg's local
ellipse and shell brightness. Locations never participate in hen identification.

Tracking follows four rules:

1. Acquire from strict detections, confirmed across three clear observations.
   Relaxed shell fragments may retain an existing egg, never create a new one.
2. Associate detections one-to-one with nearby, similarly sized remembered eggs.
   Modest rolling preserves the track ID; visible/obstructed rebounds do not
   increment production. Confirmed new IDs count once, even if another egg was
   removed and the total count did not change.
3. Retain an egg while a small smooth, similarly bright shell remnant remains.
   Intermediate evidence is uncertain, with no expiry timeout. Retirement needs
   three clear observations of almost entirely textured replacement bedding in
   the remembered shell interior, followed by the usual three-frame count
   confirmation before a collection event. A zero from the outline detector is
   not removal evidence on its own.
4. Never inspect eggs in a hen-occupied or obscured region, or with an occupied
   divider. Center remains presence-only. Failures, hidden regions and restarts
   break acquisition/removal streaks without losing confirmed egg memory.

Memory (up to 32 tracks per egg-counting region) is committed in the same atomic
nest state/split transaction as events and daily totals. New result JSON includes
`egg_tracking` for diagnostics. Status/gallery raw predictions stay frame-only;
confirmed counts and Journal egg-count labels include remembered obstructed eggs.
An obstructed Journal photo can therefore show a tracked count greater than its
raw detector count. These are interpretations, not automatic training references.

Complete burial can be visually indistinguishable from removal. Textured straw
covering the entire shell can still satisfy absence evidence; conversely, smooth
bedding can leave a removed egg uncertain. This tracker prioritizes retaining
partially visible eggs, not claiming certainty from invisible ones. No timeout
forces an uncertain egg to disappear.

Upgrade migrates nest state v5 to v6 without resetting attribution, totals or
history. Existing eggs without coordinates remain an unlocated retained baseline
until strict detections anchor them; absence alone cannot clear that baseline.
Previously inflated daily totals and false Journal entries are not automatically
repaired. Back up app data before upgrading; restore that backup to downgrade.

Regression coverage uses synthetic scenes and a modest set of authorized small
real egg/bedding crops in `tests/fixtures/egg_tracking/`; no camera capture tests
or production recognition references are added.

## Stored captures page (0.1.15+)

Select **Captures** in the MunAI sidebar to browse retained capture JPEGs.
Each page shows a single **local day**, newest photos first, starting with the
newest stored day. **Older day** and **Newer day** skip days without photos;
`captures?date=2000-01-01` can also open a specific local date. Each image has a
local-time heading. Photos are lazy-loaded
and clickable for the original, behind HA Ingress. From 0.1.17, captions show a
compact frame-only summary plus just the filename. Results are
matched by the exact capture timestamp; missing or invalid results are marked
unavailable, never inferred from the folder. Split-zone states are shown separately.
Summaries read `uncertain | empty | hen_a (0 eggs)` in left/center/right order
(top/center/bottom for horizontal splits), using only this image's raw prediction.
There are no confirmed-state/history labels or repeated zone names. One raw frame
egg count appears at the end; `? eggs` means unavailable, never a retained count
from another frame.

This is a view of the app's capture storage, not the journal evidence
archive or a list of every observation. Image predictions are not reviewed identities.
Enable `store_captures` to save future photos; deduplication and retention may
leave gaps, and a photo removed by cleanup returns not found. Browsing does not
change labels, references, retention settings or recognition.

## Confirm training examples (0.1.28+)

The small **Confirm** button retains the original image and the interpretation
shown beside it, without adding references, rerunning recognition, or changing
nest state. Use it only when that interpretation is correct. **Confirmed** means
the example was saved successfully; repeating a confirmation does not duplicate it.

Status and Captures retain the frame prediction, including left/center/right
(or top/center/bottom) labels, per-zone egg counts, the whole-frame egg count,
and any uncertainty/obscured values. Center is presence-only and has no egg-count
field in new training annotations (0.1.29+). Unavailable side counts stay null,
not zero. Existing stored annotations are not rewritten.
The separate historical **Confirmed state** is not a training label for the photo.
Journal confirmations apply only to the displayed evidence region and its label
and egg count; they do not imply confirmation of other regions or the event.
References retain their existing reviewed label and crop/full-frame scope.

Examples live privately in `/data/training/images/<sha256>.jpg` and
`/data/training/annotations/<snapshot-sha256>.json`. Images are deduplicated by
content; annotations include schema version, image checksum, source, view, layout,
region, interpretation, confirmation time, human-confirmed flag, and app version.
The full original image is retained even for a region-scoped Journal annotation.
Unknown/obscured labels are not automatically converted into hen identities.

This archive is independent of capture and journal cleanup and active references;
it is included in whole-app HA backups. Keep it private and never commit it.
The `training_storage_mb` option caps combined training images and annotations
(default 512 MiB, range 16–4096 MiB). This cap, storage reserve checks, and a
10,000-annotation limit stop new confirmations rather than deleting older ones.
Lowering the cap never deletes existing examples.
If a Status frame expires, an image changes, or the app restarts before saving,
refresh and confirm the displayed image again. Confirmation requires HA Ingress
and a server-signed image/interpretation snapshot; the client cannot substitute
a newer prediction or another label.

One pull fetches captures, paired result JSON, and confirmed training examples
into matching subfolders. References remain an explicit, separate pull.

```sh
python munai_remote.py pull local-data/production --dry-run
python munai_remote.py pull local-data/production
```

To free HA training space after saving verified local copies:

```sh
python munai_remote.py pull local-data/production --remove-transferred --dry-run
python munai_remote.py pull local-data/production --remove-transferred --yes
```

Removal needs a write-scoped transfer token. Only training files from that pull's
manifest are acknowledged after all local training files are checksum-verified
and synced to disk. HA rechecks hashes before removal and keeps shared images
needed by newer confirmations. Failed/incomplete downloads never trigger removal;
capture/result cleanup remains app-managed. Local-only files are never deleted.
For training alone, use `pull training local-data/production/training` with the
same optional removal flags. Keep a separate backup of the local dataset.

## Review & teach (0.1.19+)

The small **✎** button on Captures and Journal photos opens the same quick-review
popup. Pick a region and **Empty**, **Obscured**, an existing hen, or **＋ New hen…**.
New hen names use 1–48 lowercase letters, digits, hyphens or underscores. Inspect
the highlighted view, then choose **Confirm** to retain the original image and
edited region label in the training archive. **Add as Reference** is unchecked
by default; check it to also activate an eligible reference. The popup remembers
the last region/hen during the page session; Journal initially selects its event's
region. A **✓** indicator reopens the saved review, including from the other page
when the underlying full-frame image bytes match. Status loads as photos become visible.

- **Hen reference:** a clearly identifiable single hen in a whole frame for
  whole-box mode, or in a side/center half-frame crop for split mode. The crop is
  copied and validated before atomic activation. References are shared appearance
  examples, never position rules. Active references are picked up next recognition
  round; one example does not guarantee reliable identification.
- **Empty reference:** the **whole frame** must be hen-free; eggs are allowed.
  The original full frame becomes empty-background calibration.
- **Annotation only:** Obscured is not a trainable category yet. Partial Empty,
  whole-frame hen labels in split mode, unknown capture-time layouts, and layouts
  different from the current app configuration do not create training references.
  **Add as Reference** is disabled for these selections; **Confirm** still retains
  the edited annotation for training.

Capture layout comes from its matching result JSON; Journal layout comes from
the event archive. Missing metadata is never replaced with an assumed current
layout. Missing/expired images cannot be promoted. Failed validation leaves
references unchanged; stale image/review/reference checks require reopening the
popup. Original prediction captions, capture folders, journal events and historical
egg totals are not rewritten by reviews.

Reopen a reviewed photo and choose **Undo review** before changing that region.
Undo removes only the exact reference created by that review, checked by path
and content hash, and removes its training annotation if still present (preserving
images shared with other annotations). Pre-existing duplicate references are never removed. If another
review uses the same reference, undo that dependent review first. An interrupted
activation is shown as pending and can be undone before retrying. Undo requires
the source photo still to be available in the selected gallery. Review metadata
persists privately under `/data/reviews/` (up to 10,000 images), separate from
photo retention; include it in HA backups. Promoted references remain after the
source capture expires.

**UI teaching changes HA's active reference folder, not your local production
folder.** Before a desktop `push --replace`, pull the active references to an
ignored folder, review/merge them into `local-data/production/references/`, then
preview and confirm replacement. For example:

```bash
python munai_remote.py pull references local-data/ha-reviewed-references
```

Replacement refuses to drop or alter references attached to UI reviews. Keep
their exact paths/bytes in the merged set, or explicitly Undo the corresponding
reviews first while their source photos are still available. UI reviews cannot
silently overwrite references changed elsewhere. No new host port is exposed:
review reads/writes require HA Ingress, and writes also require a per-server
anti-CSRF token, bounded JSON and explicit confirmation.

## Active references page (0.1.11+)

In Munai's **Open Web UI**, select **References** in the MunAI sidebar. The
page shows the reference content checksum on the server, configured box split,
counts for each identity, and the original reference photos grouped by hen.
Reviewed full-frame empty-nest calibration is shown separately. Photos are
lazy-loaded and linked to their full-size JPEGs; their
filenames are visible so you can report a specific reference.

This reads the server's current reference directory, not the devcontainer's
candidate set, staging uploads or capture predictions. No reference version
history is retained. An active set can
still be incompatible with the configured recognition layout: listing it is not
proof of readiness or accuracy. Recognition picks up activated references on its
next round.

Image links include the reference content checksum. If an upload changes
the active set while a page is open, old image requests ask you to refresh rather
than silently displaying new content under an old label. All links remain behind
HA Ingress; there is no public `/local` gallery or additional host port.

Use the **×** in a photo's corner to remove it after confirmation. Removal is
permanent and affects the next recognition round, but preserves review labels,
training examples, captures, journal history and daily egg totals. Removing the
last required identity or empty calibration photo can make recognition unavailable.
Stale pages must be refreshed before removal. This changes HA only, not the local
production reference folder; pull and reconcile before uploading it again.
Use **Review & teach** on Captures or Journal photos for explicit promotion.

## Capture schedule

All HA service log messages include a local timestamp and UTC offset, for
example `2000-01-01 19:00:00 +0200 INFO: Munai started.` The timezone matches
Home Assistant's local timezone; capture filenames still use UTC.

When `capture_enabled` is true, capture and recognition run around the clock at
`capture_interval`. There is no nighttime pause or sleep mode. Retired pause
settings in saved app options are ignored on upgrade. The latest event remains
visible until another event replaces it; journal history and counts are preserved.

## Capture history and a 24-hour review window

Normal capture/result retention applies the configured age and combined byte
budget. From 0.1.10 there are separate ceilings: **1,000 photos** and **10,000
result JSONs**. Results still record every successful observation; photo
deduplication does not shorten that timeline. Before 0.1.9 both kinds shared
a 1,000-file cap, which could clip minutely observations to about eight hours.

`capture_deduplicate: true` skips similar ordinary photos. Comparison uses
320×180 grayscale pixels, brightness differences above 15, and a 0.1% changed
pixel threshold after suppressing isolated/thin detail. There are no hardcoded
camera-specific masks. Small timestamp/noise changes are usually ignored, but
large overlays or exposure changes may still save a frame. Identity/egg-count
changes (including per-zone changes), restarts, and missing comparison baselines
always save. A `capture_heartbeat_seconds: 900` default retains a photo at least
every 15 minutes while the camera loop is running. Compare with the last saved
photo, not the last observed one, so gradual changes accumulate. Failed
comparisons retain photos; failed writes do not advance the baseline.

Recognition, confirmation streaks, result JSONs and journal evidence still use
every successful observation. This is lossy ordinary-photo storage: not every
subtle visual change is guaranteed a photo. Set `capture_deduplicate: false` to
retain every frame, subject to retention limits. Journal evidence is never
deduplicated by this policy and never depends on an ordinary photo remaining.

For a review window of at least a day, start with:

```yaml
store_captures: true
capture_interval: 60
retention_days: 2
capture_storage_mb: 2048
```

The 2,048 MiB default is for new installations; upgrades do not override an
explicit existing limit. Change the byte budget in HA Configuration too. These
settings are a target, not a storage guarantee: image sizes, free disk space,
disabled capture and failed observations affect coverage. Do not disable the
free-space reserve to extend history. Archived journal evidence has separate
retention and is not a complete capture archive.

Inspect retained coverage without changing HA:

```bash
python munai_remote.py remote history
python munai_remote.py pull captures reports/my-private-history/captures
python munai_remote.py pull results reports/my-private-history/results
```

Downloads do not remove older local files, so repeated pulls preserve earlier
snapshots even after HA rotates them. No download can recover already deleted
captures. History summaries show oldest/newest UTC timestamps, counts and bytes;
they do not prove uninterrupted coverage between those endpoints.

## Split nests and divider occupancy (experimental, 0.1.10+)

Set HA `box_split` to `none` (default), `vertical` (left/right), or `horizontal`
(top/bottom). This is an HA app option, not a setting in the legacy Pi `.env`.
Splits are fixed 50/50. Detection also considers the center/divider. Identity
always comes from a shared appearance reference pool, never from the side, body
orientation, or missing positional coverage. Half-frame processing preserves
the original 640×360 scale (320×360 for vertical, 640×180 for horizontal), rather
than stretching every crop back to a full frame.

Each side has its own durable visit, egg baseline, three-frame arrival/count and
five-frame departure confirmation. Center visits have presence/identity events
but no egg production. A divider hen or ambiguous merged/multiple bodies pauses
hidden-side observations and invalidates unsafe laying attribution; no empty
observation or collection is fabricated. Patterned bodies missed by smooth
regions can pause counting through reviewed-empty background-change checks.
New bedding/lighting can cause conservative unknown/obscured outcomes and may
need refreshed reviewed empty calibration. From 0.1.18, reference and live views
share area resampling and the normalized split grid. Background-change checks
suppress fine edge noise; broad losses of reviewed texture can still reveal
similarly bright bodies, subject to the feather/smooth-patch gate that rejects
smooth egg piles. These cues establish presence/uncertainty, never identity.
A working assumption that divider
hens are alone does not override contradictory observations.

From 0.1.27, a detected body's position is the equal-size side or center view
containing the most body-mask pixels, not the largest bounding rectangle or an
identity feature. A side wins a tie with center. Separate bodies are never
combined just to choose a center position.

A known hen has one confirmed position. Three confirming observations in a new
zone transfer her position and record e.g. **Hen_a moved to center**, rather than
another arrival and a later departure from the old zone. Obscured shots pause
confirmation; failed rounds and restarts reset it. Fresh confirmation also
reconciles duplicate positions retained by older releases. Two separate bodies
predicted as the same hen stay uncertain; body size is not an identity tie-breaker.
Unknown hens cannot be linked across zones by appearance-free assumptions.

A transfer does not prove the former nest empty or make hidden eggs countable.
Its last clean egg count remains available while presence is reconfirmed, but
unsafe source/destination laying attribution is discarded. Multi-region decisions
use `/data/nest-transition.json` for interrupted-write replay, so moves cannot
persist just one half of a transfer. Existing journal entries and totals are not
rewritten; include app data in backups before upgrading.

From 0.1.18, the minimum detected visitor surface is
`max(chicken_area, 10 * egg_max_area)` at the normalized 640×360 scale (35,000
pixels with default egg settings, with no half-frame stretching). `chicken_area`
still controls minimum measurable plumage in reviewed identity references; it
does not lower this live visitor floor. Smaller convincing foreground bodies,
including edge fragments, are **obscured**, not identity/visitor observations.
An obscured shot neither advances nor resets the three/five-shot confirmation
counters. Small fragments are skipped without changing the visit baseline;
genuine hidden/ambiguous nests still invalidate unsafe laying attribution.

From 0.1.25, egg detection runs independently for each **clear, hen-free side**
when the divider is clear. A hen or local obstruction freezes only that side;
divider occupancy or ambiguous whole-frame presence blocks both sides. The center
is presence-only and never contributes an egg count or laying event. The two
non-overlapping side counts are summed; any hen can earn eggs from either side
through that side's confirmed visit and subsequent egg increase. Uncertain
attribution counts as unknown, not a guessed identity.
Uncountable shots do not enter egg-confirmation evidence. Clear presence
observations can still confirm departure while egg counting is frozen; a later
clean stable count may confirm a pending egg. Failed captures and restarts remain
separate gaps that reset confirmation. None of these rules backfill past events.

The journal prefixes events with their zone and outlines the corresponding view
on shared full-frame evidence photos. The outline is a view, not an exact body
boundary. Each event retains its own real confirmation/baseline frames; simultaneous
events may share one physical JPEG. All region outboxes replay idempotently after
crashes, and active evidence from all regions is protected from archive cleanup.
Every caption shows the full JPEG filename: use **filename plus zone** when
reporting a wrong identity. From 0.1.19, **Review & teach** can explicitly annotate
that region and promote a validated copy to references. The original prediction
and shared journal photo remain unchanged; no historical egg totals are rewritten.
MQTT discovers side occupant/egg-count sensors plus a center occupant sensor;
the original whole-device state includes a zone summary and combined confirmed
egg count. Counts remain frozen when hens obscure the nest.

### References and safe migration

1. Back up the app data, disable capture, and install the new release.
2. Prepare **half-frame identity crops** for all hens. A divider hen uses the
   centered half-width/half-height view, not a separate identity. Preserve original
   full-frame references for review/backup. Review clipped crops: too little
   measurable plumage is not a usable reference, even with a correct human label.
3. Keep **full-frame reviewed empty references**, not half crops, in `empty/`.
   A full frame whose two halves were explicitly reviewed as empty is suitable;
   never use a prediction or divider hen as empty calibration. Background masks
   are shared neutrally for appearance, not used to assign identities.
4. Set `box_split` while capture remains off. Upload the complete prepared set,
   explicitly overwriting old full-frame identity files. Preserve their original
   filenames for replacements. New reference names may append `-left`, `-right`,
   `-top`, `-bottom` or `-center` after the UTC timestamp; these are provenance
   only. Check `remote status` reports compatible/ready references before capture.
5. First split counts establish fresh baselines without crediting existing eggs
   as new production. Today's whole-box production totals carry forward once,
   independently of new side counters. Whole-box visit baselines are never copied
   into either side.

Split persistence lives under `/data/nests/<layout>/<zone>/`, with dated carry
and latest-event metadata in `/data/nest-layout.json`. Changing split axis or
returning to whole-box mode fails closed if incompatible split state exists.
Restore the matching configuration/state backup rather than deleting arbitrary
files or mapping old baselines. Reference geometry and state must match the mode.

Validate on held-out reviewed footage before enabling a supervised pilot.
Adjacent frames from a reference visit and self-included reference predictions
are not independent accuracy measurements. Unknown is preferable to an invented
identity; synthetic mechanics tests cannot guarantee field accuracy.

### Offline review and replay

```bash
python tools/review_box_split.py captures/pending --references local-data/production/references \
  --split vertical --output reports/split-presence-review
```

That first tool shows presence geometry only, not the complete app pipeline.
Replay original timestamped full-frame photos through the complete split pipeline
into a new private local results/journal directory:

```bash
python tools/replay_split.py captures --references PATH_TO_PREPARED_SPLIT_REFERENCES \
  --split vertical --output reports/split-replay
```

Open `reports/split-replay/journal/index.html` to review reconstructed events.
Raw and confirmed per-zone observations are in `reports/split-replay/results/`.
This is a new algorithm replay, not recovery of historical decisions. Sources,
labels, active references, HA and MQTT are untouched. All generated data stays
private under `reports/`; do not commit it.

## Offline egg counting

Run the experimental OpenCV counter in the devcontainer:

```bash
python count_eggs.py
# Or choose a folder and a NEW report directory:
python count_eggs.py captures/eggs --output reports/egg-review
```

It reads JPEG/PNG images directly inside `captures/eggs/`, including manually
renamed files. It never moves or modifies originals, reads credentials, accesses
the camera, or publishes to HA. Dependencies are the existing OpenCV and NumPy
installation; the script's syntax supports Python 3.5 with compatible versions
of those libraries (do not install the modern HA pins on the legacy Pi).

The default output is a new, ignored `reports/eggs-<UTC timestamp>/` directory:

- `counts.csv`: per-image status, visible-egg estimate, boxes in 640×360
  coordinates, and detector thresholds. A hen-present frame has a blank count,
  **not zero**. `partial_detections` lists the one-based detection numbers whose
  contours touch the image edge; `smooth_fractions` records how much of each
  candidate's visible region passes the texture test.
- `annotated/0000.jpg`, etc.: numbered detection overlays, matching CSV row order.
  Captions appear below the original frame, not on top of bottom-edge eggs.
- `sheet-001.jpg`, etc.: paginated contact sheets for visual review.

Reviewed `empty/` images in the current reference folder are required; use
`--references PATH` for a different reference root. Their background mask is used
only by the existing hen-presence heuristic. Egg detection itself does not use
that mask: an egg visible in an empty reference must not be masked away.

Hen presence uses the same check in recognition, pre-sorting, offline egg counting,
and HA. A region must first meet `chicken_area`, then contain either an unclosed
smooth patch with radius at least 22 pixels or less than 75% smooth surface
(feather-like texture). Thickness is measured **before** closing joins nearby
shells: a large, mostly smooth egg pile is not enough on its own. If the largest
region is rejected, smaller regions are still checked so a pile cannot hide a
hen elsewhere. These are presence cues only, never identity features. Hens still
suppress all egg processing. Thresholds refer to 640×360 normalized images and
remain experimental, not guarantees for new lighting or poses.

The counter finds locally smooth, egg-sized regions. At least 75% of a
candidate's visible surface must pass the texture test; size bounds apply to
both its visible area and fitted ellipse. It fits the outer convex envelope
rather than penalizing every straw notch or jagged contact seam. Touching
candidates are separated using distance-transform peaks with a substantial neck
between them, so small notches do not automatically split one egg into two.
From 0.1.44, at least 70% of the visible contour perimeter must follow the fitted
ellipse within a 15% radial tolerance. Straight image borders provide no shell
evidence; an egg-shaped convex hull alone is insufficient.

No exclusion buffer is applied at picture edges or split dividers. Mostly visible
border-clipped eggs can be counted if at least 70% of the fitted ellipse area is
supported by the visible region and the curved-boundary check passes.
Border-connected components are never split into smaller candidates, to avoid
turning walls into eggs. Hen presence and identity recognition are unchanged.

`--texture-threshold`,
`--min-area`, `--max-area`, and `--chicken-area` are experimental controls in the
normalized image space. Defaults are generic starting points, not a validated
camera calibration. Keep any deployment-specific measurements/settings private.

**Limitations:** heavy occlusion/clipping, changing light, smooth
straw/walls, and missed hens can produce wrong estimates. Review the overlays;
zero means no candidates detected, not proof that the nest is empty. Synthetic
tests verify mechanics, not real-world accuracy. Compare with manually reviewed
counts and include genuinely egg-free and hen-present frames before deployment.
Do not sum counts across frames: the same egg appears repeatedly. The offline
tool does not publish MQTT; the HA app uses the same detector for its Egg count
sensor. Neither detects laying events or identifies the laying hen.

## Validation status

The committed image regression suite contains 24 reproducibly drawn scenes:
4 empty, 4 separated-egg, 5 touching-pile, and 11 hen images. It checks both smooth
and patterned plumage, clipped/smaller hens, hens alongside eggs or larger piles,
and integration through HA and offline egg-count gating. See
[`tests/fixtures/nest_presence/README.md`](tests/fixtures/nest_presence/README.md)
for provenance, regeneration, and count limitations. Tests read the committed
JPEGs; they do not regenerate images just to make a changed detector pass.
Camera-derived data stays private and is not covered by the public-fixture
exception in the path check. Review independent real images locally as well:
synthetic regression success is not a field-accuracy measurement.

Offline tests exercise real local HTTPS, synthetic reference upload/download,
authorization, pinning, atomic replacement, persistence, retention, MQTT payloads, and CLI
behavior with synthetic owner-only credential files. They do not access real
credential files, `.env`, cameras, brokers, or HA. CI builds both ARM64 and AMD64
images and smoke-tests them without networking. **A configured workflow is not
evidence of a successful CI run.** Supervisor installation,
ARM64 performance, and Green CPU/RAM usage still need a supervised pilot.

A local **x86_64/Python 3.10.18** synthetic benchmark (20 generated 1920×1080
references, 30 recognition calls, one OpenCV thread) loaded references in 0.484 s,
averaged 0.0244 s per recognition, and peaked at 76.6 MiB process RSS. This excludes
FFmpeg, MQTT and server load, and is **not** a Green capacity measurement. Binary
wheels for all pinned dependencies were downloaded successfully for CPython 3.13
ARM64; executing those wheels still requires CI/ARM64 validation.

Do not enable automatic updates during the pilot. Builds themselves consume
resources on the Green. No container isolation can guarantee HA responsiveness.

## 1. Publish and install (manual approval gate)

These are instructions for you, not actions already performed by the agent.
Publishing, connecting to your HA, and enabling capture require your approval.

1. Publish the sanitized repository to the chosen **public GitHub repository**.
   Do not publish the old recovery bundle, old refs, `.env`, captures, recognition
   reports, camera calibration, models, or any local exports. Keep the repository
   URL stable after installing: changing it creates a different HA app identity.
2. Check the GitHub **Check app** workflow succeeds for tests and both platforms.
3. In HA, open **Settings → Apps → Install app → ⋮ → Repositories** (called
   **Add-ons → Add-on Store** on older versions). Add the public repository URL.
4. Install **Munai**. Leave protection mode enabled. The app requests no host
   mounts, host networking, hardware, Docker socket, HA API or Supervisor API.
5. Keep `capture_enabled: false` and `mqtt_enabled: false`. Under **Network**,
   assign host port **8443** to container `8443/tcp`; the port is off by default.
   Use another free host port if necessary, and use that port in the CLI URL.
6. Start the app and open **Logs**. Expect a startup summary, a certificate SHA256
   fingerprint, and “Transfers locked.” No camera or broker connection occurs.
   Copy the fingerprint directly from these trusted HA logs.

Transfers bind HTTPS inside the container. Only expose the mapped port to your
trusted LAN/VPN; **never forward it through your router**. There is no unauthenticated
token-provisioning endpoint, HTTP downgrade, ingress token workaround, or HA token.

## 2. Pair from the devcontainer

Run directly in the **devcontainer terminal**, from this checkout. The transfer
CLI uses Python's standard library: no separate venv, activation, pip install,
Keychain, wrapper, OpenCV, or FFmpeg is needed.

```bash
python munai_remote.py remote pair https://homeassistant.local:8443 --fingerprint FINGERPRINT_FROM_HA_LOGS
```

Pairing checks the server certificate **before sending any HTTP headers**,
generates a random 256-bit token, and saves it in **`.munai-remote.json` beside
the script**, together with the address and certificate fingerprint. This is an
explicitly approved **plaintext local secret**, like `.env`, with owner-only
permissions (`0600`). It is gitignored and excluded from the container image.
Only the token's **SHA256 digest** is printed; the raw token never appears in
arguments or logs. Do not paste, commit, share, or inspect the credential file
with agent tools. Anyone with access as your user/root can read it; protect the
machine and any backups accordingly. Permission checks reject symlinks and
group/world-readable credential files.

Use an HA address reachable from the container. If `homeassistant.local` does not
resolve there, replace it with HA's LAN IP/DNS name; certificate pinning still
checks the same fingerprint before transmitting any token.

In HA's **Configuration** form, use the ordinary primary-client fields:

**Enable “Show unused optional configuration options”** if the token field is
hidden. If it is still absent, check Munai's **Info** tab: these fields require
**0.1.2 or newer**. Reload the app repository and update the app if necessary.

- **Transfer client name** (`transfer_token_name`): for example, `desktop`
- **Transfer token SHA256** (`transfer_token_sha256`): paste the printed digest,
  not the certificate fingerprint; this field is optional until pairing
- **Transfer client permissions** (`transfer_token_scope`): `write` for uploading;
  `read` permits only status/downloads

Leave **Additional transfer clients** empty unless you need multiple clients.
That optional field accepts text rows `name|read-or-write|SHA256`, without JSON or
brackets. Clearing the primary digest and leaving additional clients empty locks
all authenticated transfers after restart.

Save and restart the app. Then run:

```bash
python munai_remote.py remote status
```

It should show the app's reference counts and operating modes. The default profile
is `green`; use global options **before** the command to select another profile.
`--config` selects a different **secret** credential JSON file: choose an ignored
location such as `local-data/`, never `.env` or a tracked file.

```bash
python munai_remote.py --profile second-nest remote status
```

The public certificate and its private key persist under `/data/tls`. Trust uses
the exact certificate fingerprint instead of public CA/hostname validation. If
it changes, the CLI refuses to send the token. Investigate through HA's trusted
logs; do not accept a new fingerprint supplied by an untrusted network endpoint.

## 3. Configure recognition and upload reviewed references

Include explicitly reviewed no-hen JPEGs in the reference set's `empty/` folder.
Eggs are allowed. Use several examples covering lighting and straw changes.
At 640×360 resolution, pixels smooth in a majority of these images are excluded
from the feather detector, so walls do not become false hens. Calibration is
computed in memory when references load; there are no generated cache files.
Hen references and captures use the same calibration. Coordinates never identify
a hen. Recognition no longer reads `camera-profile.json` or offers manual masks.
Re-review empty references if the camera moves or the background changes materially;
this texture heuristic is not a general-purpose object detector.

The same flat reference layout is used locally and on HA (data root `/data`).
The canonical editable local folder is `local-data/production/references/`;
the older checkout-root `references/` is only a non-production archive of reviewed
images whose SHA256 contents were absent from canonical at cleanup time. It has
no version directories or state pointer. Do not merge or deploy it automatically.
For shared modules' standalone command-line entry points, explicitly pass
`--references local-data/production/references` when reviewing production data.

```text
<data-root>/
  references/
    hen_a/20000101T120000_000000Z.jpg
    hen_b/20000101T120100_000000Z.jpg
    empty/20000101T120200_000000Z.jpg    # reviewed no-hen background, eggs allowed
  scratch/                             # disposable upload/activation staging
  captures/
    pending/                           # unclassified standalone/offline frames
    empty/
    eggs/                              # optional legacy/offline egg candidates
    unknown/                           # detected hen, identity not known
    hen_a/unreviewed/                   # named automatic predictions
    hen_a/reviewed/                     # human-confirmed correct predictions
    hen_a/incorrect/                    # human-reviewed wrong predictions
  results/
  reports/                             # optional historical/offline reports
```

These names and dates are synthetic examples. Your existing real identity folder
names and UTC capture filenames are used at runtime without entering Git. Folder
names must be lowercase letters, digits, underscores or hyphens, start with a
letter/digit, and contain at most 48 characters. Names `unknown`, `unreviewed`,
`empty`, `eggs`, `pending`, `no_chicken`, `unavailable`, `versions`, `staging` and
`state` are reserved. Only timestamped JPEGs directly
inside identity folders qualify. The reserved `empty/` reference folder calibrates
background only; it is never a hen identity. Nested predictions and `captures/empty/`
never become references without explicit review and import.

### Manual capture review convention

Move checked predictions from `captures/<identity>/unreviewed/` into the same
identity's `reviewed/` directory when correct, or `incorrect/` when wrong.
**Both destinations mean the image has already been reviewed.** In `incorrect/`,
the parent directory records the rejected prediction, not the true identity;
the correct identity may still be unknown. Leave these review decisions intact
when processing new unreviewed captures.

Review status is separate from reference membership. Neither directory is
automatically loaded as references. Confirmed images can be explicitly staged
and imported using the reference workflow below; incorrect images require an
explicit corrected label first. Keep incorrect examples for later error analysis
and recognition improvements rather than silently relabeling or discarding them.

```bash
python munai_remote.py push references --dry-run
python munai_remote.py push references
python munai_remote.py remote status
```

The server validates each JPEG and checks it contains a measurable hen using
the reviewed empty references/thresholds. Empty references are reviewed background,
not identities. It checks SHA256, rejects symlinks and unsafe
paths, stages the change, and atomically activates it **between recognition
rounds**. Readiness requires reviewed **empty images and two hen identities**.
Include varied appearances/poses; missing nest positions are never identity evidence.

Uploads read only JPEGs directly inside reviewed identity and `empty/` folders,
never nested predictions. Legacy versioned import sources are still readable for
migration, but new managed stores are flat and have no state pointer or history.

### Canonical editable production references

Edit production references directly in `local-data/production/references/`, with
JPEGs immediately inside each identity folder or `empty/`. This is a plain,
ignored folder: no local version directories, state pointer, or snapshot manifest.
Keep it separate from report candidates and the working managed `references/`.

Local corrections are not deployed automatically. Preview and explicitly upload:

```bash
python munai_remote.py push local-data/production/references --replace --dry-run
python munai_remote.py push local-data/production/references --replace --yes
python munai_remote.py remote status
```

`--replace` makes production exactly match this folder, including removals and
relabeled images. Review the preview counts, then confirm with `--yes`. All
JPEGs are checked against the complete staged set before one atomic directory
exchange, between recognition rounds. Upload or validation failure before exchange
leaves production unchanged. Concurrent edits cause a checksum conflict. The CLI
verifies the resulting remote paths and file checksums. Replacement needs reviewed
empty references and at least two hen identities. HA 0.1.13 or later is required.

There is no local or production reference versioning and no rollback operation.
Temporary old data is discarded after the swap (or cleaned on restart after a
crash). Export a backup before major edits if you need to undo them. Startup
automatically migrates old managed stores, keeping only their active images.
The API's legacy `revision` field now means a content checksum for concurrency
and cache invalidation, not a saved version. Ordinary push without `--replace`
still adds/updates one file at a time and never removes remote-only files.
Never commit private images or assume pending local edits are already on HA.

### Adding reviewed references

Copy explicitly reviewed JPEGs directly into the appropriate identity or `empty/`
folder under `local-data/production/references/`, then preview and confirm a
complete replacement using the commands above. Never import predictions without
review. The old standalone migration, import, and batch-review utilities have
been removed. HA's startup migration from its former versioned layout remains.

Unchanged files are skipped. No files are deleted. Different contents under an
existing name require `--overwrite`; inspect the conflict before using it. Each
file is an atomic change, **not an all-or-nothing batch**: an interrupted push may
have activated earlier files. Rerun it safely to finish. Concurrent changes cause
a checksum conflict instead of overwriting another upload. For all-or-nothing
replacement including deletions, use `--replace` as described above.

Limits: **8 MiB per JPEG, 16 million decoded pixels, 8192 per dimension, 500
references and 128 MiB per reference set**. Replacement uses an uncompressed ZIP
bounded to 129 MiB; compressed/encrypted entries, links, duplicate paths and unsafe
paths are rejected. Only the current set is stored. Interrupted temporary staging
is cleaned at startup. Keep originals on this machine too.

## 4. Configure MQTT, then capture

1. Set `mqtt_host` (default `core-mosquitto`), `mqtt_port`, `mqtt_user`,
   `mqtt_password`, and `mqtt_tls` in the HA form. TLS uses system CA trust and
   hostname validation, with no insecure mode. Plain MQTT is unencrypted: restrict
   it to a trusted network. No Supervisor MQTT credential lookup is required.
2. Keep a stable `device_id` and choose `device_name`. The default ID
   `munai_ha_nest` differs from the legacy cron device to avoid discovery conflicts.
   Enable `mqtt_enabled`, save and restart. The HA MQTT integration should discover
   the device and its **Occupant** and **Egg count** sensors, initially unavailable.
3. Set `camera_url` (masked password field) to your credential-bearing RTSP URL.
   The app never reads the developer `.env` or logs the URL.
4. During a supervised pilot, enable `capture_enabled`, save and restart. Start
   with the default **60-second** interval and **20-second** capture timeout.
   Run only one active capture schedule against the camera: leave the cron option
   available, but disable its schedule when testing HA capture if both use it.
5. Watch HA responsiveness and the app logs. Only enable **Start on boot** after
   the pilot succeeds. Do not assume CPU or memory resource limits are enforced.

The Occupant sensor reports the identity folder name with its first letter
capitalized, `empty`, or `unknown`. It becomes HA `unavailable` after `expire_after`
seconds without fresh state, on disconnect, or on app shutdown. `expire_after`
must exceed `capture_interval + capture_timeout + 10`; default 180 seconds.
Discovery and availability are retained; **observations are not retained**, so
HA restarts do not replay old identities as fresh. HA's birth message triggers
rediscovery; the next successful capture supplies a fresh observation.

An unidentified, visible hen triggers up to 15 extra shots in the same round,
within the existing `capture_timeout` budget. Retrying stops once visible hens
have accepted identities. One accepted identity wins over unknown shots; the
original appearance distance/margin thresholds are not relaxed by retries.
Split regions retain their own identified shot and journal evidence while other
regions retry.
Empty or obscured observations discard that region's earlier retry identity;
obscured regions do not themselves trigger retries. If no shot identifies a hen,
it remains unknown. Retry shots do not count as separate arrival/departure
confirmations. Unknown presence can complete an identified arrival's three-round
confirmation, but cannot override an already known visitor. Empty frames,
competing identities, and interrupted observations reset that candidate.

From 0.1.34, a measurable split hen that fails the original margin check can
receive a second opinion from rotation-invariant feather texture and local
brightness/texture patches. Both matchers must select the same identity with a
best-to-next-identity distance ratio at most 0.8 and an absolute distance within
`max_distance`. Existing accepted identities stay unchanged; an upgrade cannot
claim a hen already accepted on the other side. Obscured/uncertain presence and
insufficient plumage stay unknown. All comparisons use the same shared reviewed
reference pool, never nest position or orientation. Whole-box mode is unchanged.
Reference descriptors are cached in memory, not written into the reference
folders. Extra live descriptors are computed only for measurable unknown hens.
Raw upgraded zones carry `identity_method: plumage_consensus`; their distance and
margin retain the original seven-feature diagnostics, not second-opinion scores.

From 0.1.41, split mode also checks multi-scale feather texture when smooth body
patches are insufficient. A bounded in-memory dictionary of reviewed empty-nest
textures supplies the background comparison; it contains no identities or patch
coordinates. Broad, textured foreground can supply presence and reference/body
measurements without lowering `texture_threshold` or identity confidence checks.
Thin fragments and smooth egg piles remain excluded. Reference upload validation,
reference loading and live measurements share this fallback. A newly measurable
photo may still be unknown if its appearance does not match reviewed references;
reviewed photos are never added automatically. Whole-box mode is unchanged.

From 0.1.36, unexpected runtime failures include a safe `Diagnostic:` in app logs:
exception categories, public application file/line locations, supported OpenCV
function/error codes, and NumPy/OpenCV versions. Wrapped recognition errors retain
these diagnostics, while expected reference validation errors keep their specific
actionable message. Identical failures are throttled for 15 minutes; changed causes
are logged immediately. Raw exception messages, traceback source lines, locals and
private paths are deliberately omitted because they can contain credentials or
private image names. Technical diagnostics stay in logs, not the app error list.

Egg count shares Occupant's observation, availability and expiry. Occupant uses
three-observation arrival/identity confirmation and five-observation empty-nest
departure confirmation, with the same unknown-identity handling as events.
Egg count changes require three consecutive empty images with the same count,
including positive counts and zero. Known and unknown hens suppress egg detection
and preserve the last confirmed count, rather than replacing it with zero.
Until established, occupant and egg count are unknown (JSON `null`). Departure
can be confirmed before the count settles; a later confirmed increase can still
produce a laying event for that visit. The last visitor remains eligible even
after an unchanged count settles, including a zero that missed the egg. A later
increase can therefore produce `Hen_a left` followed by `Hen_a laid an Egg`.
Attribution and its evidence survive restarts, but confirmation streaks restart.
A new confirmed visit replaces the previous visitor; if it occurs before the
post-departure count settles, it starts without a known egg baseline. Confirmed
collection ends the previous attribution window. Missing baselines and ambiguous
swaps are never resolved by guessing an identity from the latest event text.
Detection failures fail
the observation rather than publishing a fabricated zero. Counting works even
with `store_captures` disabled and never changes capture categories or references.

Configure `egg_min_area` and `egg_max_area` in HA using measurements from reviewed
egg-free and egg-containing images. They correspond to offline `--min-area` and
`--max-area`; `texture_threshold` is shared with presence detection. Defaults are
generic starting points, not deployment calibration. The count is a measurement,
**not a cumulative total**. Results JSON and transfer status's `last_observation`
also include confirmed `egg_count`, occupant and latest event; `raw_prediction`
keeps the unfiltered occupant/count separately for diagnostics (occupied raw
counts are placeholders, not measurements). The legacy Pi/cron publisher and
offline image-review results remain unchanged.

The broker must permit publishing to:

```text
homeassistant/sensor/<device_id>/occupant/config
homeassistant/sensor/<device_id>/egg_count/config
homeassistant/sensor/<device_id>/daily_eggs/config
homeassistant/sensor/<device_id>/daily_eggs_<identity>/config
munai/<device_id>/state
munai/<device_id>/availability
munai/<device_id>/daily_eggs
```

and subscribing to `homeassistant/status`. The non-retained state JSON contains
both `occupant` and integer `egg_count` (or `null` until established). Eggs remain ignored when identifying
occupants; egg counting does not influence hen identity.

### Daily production counts and InfluxDB

`munai/<device_id>/daily_eggs` publishes a **retained, QoS 1 absolute snapshot**:

```json
{"date":"2026-11-02","counts":{"hen_a":1,"hen_b":0,"unknown":2}}
```

Keys are the exact identity folder names, without display capitalization. Reviewed
identities are initialized to zero; `unknown` is always present. Identities already
counted remain in the object if references change, so their totals are not lost.
The date uses the service's local/Home Assistant timezone.
Counts reset on the first scheduler iteration of a new day, even while capture
is disabled. An offline service cannot publish a midnight reset; always
check the payload date rather than assuming a retained payload describes today.

Every confirmed visible-egg count above the **highest confirmed count since the
last confirmed zero collection** adds only the excess. This prevents stable partial
detection dips and rebounds from counting the same egg twice, including across
restarts and daily rollover. Actual partial collection is unsupported: production
may be undercounted until the old high-water count is exceeded or zero is confirmed. A
reliably attributed visit credits the hen; unidentified visits, ambiguous hen
swaps, increases without an observed visit, and eggs in the first ever confirmed
empty-nest count credit `unknown`. First-observed eggs may have been laid before
monitoring started. Upgrading existing visit state does not count eggs already in
its baseline again. These are inferred/first-observed production totals, not
guaranteed biological laying dates. Eggs first confirmed after midnight belong to
the new day, even if the visit began the previous day.

Collection and other decreases never subtract from production. Repeated stable
counts do not increment it. Totals, visit transitions and pending attribution are
committed together in `nest-state.json` before MQTT publication. Restarts, failed
writes, repeated captures and MQTT retries cannot replay a committed increase.
If the local date moves backwards, counting fails closed until the clock/timezone
is corrected instead of silently resetting or duplicating totals.

Keep the existing MQTT-to-Influx bridge: write each `counts` value as an **absolute
gauge**, with chicken and payload date identifying the daily series. Use `last()`
or `max()` for daily totals, **not `sum()` of repeated snapshots** and not `+1` on
message receipt. MQTT duplicates/reconnects then cannot inflate counts. The bridge
is responsible for history; this topic only retains the current day, not a durable
archive or delivery queue for previous days. If it misses an entire day or the
broker remains unreachable through rollover, that day's final total cannot be
recovered from this topic. Back up application data as well as InfluxDB.

Counts remain camera-based estimates. Eggs hidden during occupancy, simultaneous
laying/collection, missed activity during downtime, and persistent detection errors
cannot be reconstructed reliably. A falsely confirmed zero followed by an
increase can look like collection followed by new eggs. Confirmation reduces this
risk but cannot guarantee every physical egg is observed or correctly attributed.

**Home Assistant UI:** MQTT discovery adds **Eggs today** (sum of all buckets),
one **<identity> eggs today** sensor per hen, and **Unknown eggs today**, all under
the existing device. Each has a date attribute; the total also exposes the counts
object as attributes. Add these entities to an **Entities** or **Tile** dashboard
card using HA's UI—no custom JSON display/card is required. Entity IDs are assigned
by HA; select the discovered entities rather than assuming an ID. These dated
totals have no capture expiry, but share device connectivity availability. Existing
Occupant and visible Egg count sensors retain their expiring live-state behavior.

One Python scheduling loop runs serial rounds, uses monotonic deadlines, skips
missed rounds instead of catching up, and waits interruptibly on shutdown.
An in-progress FFmpeg call may finish or hit its bounded timeout before shutdown.
Reference features are cached until the active version changes. OpenCV/BLAS use
one worker thread. Training and continuous video processing are not included.

## Storage and downloads

`store_captures` defaults to false. Recognition still uses a temporary JPEG that
is removed after the round. When enabled, images are saved atomically under:

```text
/data/captures/empty/<timestamp>.jpg
/data/captures/unknown/<timestamp>.jpg
/data/captures/<identity>/unreviewed/<timestamp>.jpg
```

Results are small JSON files in `/data/results/`; they never contain camera/broker
credentials or reference paths. Both captures and results are subject to the
combined `capture_storage_mb` cap (default **256 MiB**), `retention_days` (default
**7 days**), and **1000 total file** cap. Retention deletes oldest managed files;
it never touches reviewed references. A minimum free-space reserve (default
**128 MiB**) blocks new captures/uploads before the disk is exhausted. Temporary
files or unmanaged external disk use can still exceed these application limits.
These retention policies run in the HA service, not against local offline datasets.
Local originals are not purged as part of preparing a review.
Hidden local locks/OS metadata are neither exported nor counted as image data.

### Offline diagnostics

The split-specific `tools/review_box_split.py` and `tools/replay_split.py` remain
available for preparing and inspecting split recognition (see the split workflow
above). `tools/generate_presence_fixtures.py` regenerates synthetic test fixtures,
and `tools/check_public_tree.py` checks tracked paths for private data. The shared
capture and recognition modules remain because HA imports them; their standalone
entry points are not a replacement for the removed Pi/cron MQTT runner.

Use an ignored `local-data/` destination for exports:

```bash
python munai_remote.py pull references local-data/reviewed-backup
python munai_remote.py pull captures local-data/captures --dry-run
python munai_remote.py export local-data/export
```

Downloads verify checksums and activate atomically. Different existing local files
require `--overwrite`; no local files are deleted. A download during reference
changes or capture retention may fail safely; rerun it. For a consistent full
backup, disable capture and avoid uploads, or use HA's cold app backup.

There is no reference rollback or retained version history. Export reviewed
references before major changes; restore by explicitly replacing the set from
that backup. There is no arbitrary remote file deletion, file/configuration
upload, unrestricted archive extraction or executable
model loading. Runtime settings belong in HA's form; model transfer is deferred
because this recognizer uses references, not trained models.

## Backups, updates, and revocation

- Include Munai in an encrypted **HA backup before updates or uninstalling**.
  Backups are cold: HA stops the app to capture consistent `/data`. They include
  current reviewed references, optional captures, results, TLS keys, and private options.
- Updates rebuild the app locally and preserve `/data`. To roll back code, restore
  the known-good app backup, which also restores that backup's reference set.
- **Uninstalling can remove all `/data`**, including references and TLS identity.
  Local CLI exports contain references/captures/results, **not** options, tokens or
  TLS keys, so they supplement rather than replace an HA backup. Restoring only
  images to a fresh installation requires pairing with its new certificate.
- To revoke the primary client, clear `transfer_token_sha256`; for an additional
  client, remove its row from `transfer_tokens`. Save and restart
  the app. Then run `remote logout` locally. Logout alone does not revoke the
  server-side token. To rotate, revoke/logout and pair again.
- If previously paired using the old native Keychain CLI, revoke that old digest
  in HA and pair again from the container. The new CLI does not access or delete
  host Keychain entries. `.munai-remote.json` persists across container rebuilds
  in the workspace; it is not an HA backup or an image export.
- If the new app causes trouble, stop it and restore the old cron schedule. Keep
  the legacy Pi references/configuration backed up.

## Logs and troubleshooting

### Upgrading to reference-based background calibration

Manual boundaries and exclusion settings have been removed. Old mask options are
ignored on upgrade. Import reviewed no-hen images into `empty/` in your active
reference set and push references before enabling capture. Missing empty references
stop recognition rather than silently using uncalibrated wall detections.
Reload the app repository and reopen Configuration; do not reset camera/MQTT settings.
For an old token object, copy its digest/name/scope into the new primary fields,
or convert additional clients to the optional text-row format above. No actual
token needs to be exposed or regenerated just to change the form.

INFO logs show startup modes, the public certificate fingerprint, reference reloads,
occupant changes, actionable failures, and recovery. Identical failures repeat at
most every 15 minutes. There are no per-request access logs, per-frame success logs,
credential dumps, raw FFmpeg diagnostics, or raw exception tracebacks. `log_level:
debug` adds round timing only; it does not expose secrets.

- **Cannot reach HTTPS:** check app status, HA Network mapping and LAN/VPN access.
- **Fingerprint mismatch:** stop and verify the new public fingerprint in HA logs.
- **401:** check the token digest/scope, save options and restart; do not paste tokens
  into commands or support logs.
- **Reference rejected:** inspect the JPEG, filename, empty references, and recognition
  thresholds; a file must contain enough measurable body and background.
- **Device missing:** enable MQTT, check credentials/ACLs and HA's MQTT integration.
- **Unavailable:** inspect capture/MQTT settings and freshness. Never interpret a
  failed capture as an empty nest.

## Developer checks (devcontainer only)

Use a separate modern virtual environment; leave legacy Pi requirements alone:

```bash
python -m venv /tmp/opencode/munai-ha-venv
/tmp/opencode/munai-ha-venv/bin/python -m pip install --only-binary=:all: -r requirements-app.txt
/tmp/opencode/munai-ha-venv/bin/python -m unittest discover -s tests
/tmp/opencode/munai-ha-venv/bin/python tools/check_public_tree.py
git diff --check
```

Python 3.13.18 is the container/CI target. Tests can also run on the devcontainer's
Python 3.10 with the pinned libraries; that is not ARM64/Supervisor validation.
The public-tree check only checks tracked paths, not source-text secrets. Review
all staged changes and all history before publishing. Never add real names or
images to test fixtures. CI builds images without publishing to a registry.
