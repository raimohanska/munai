# Changelog

## Unreleased — developer cleanup

- Remove the obsolete standalone Pi/cron MQTT runner, its dependency file,
  local layout/import/batch-review tools, and their dedicated tests and guides.
  Keep HA's shared capture/recognition modules and split-specific diagnostics.
- Document the old root-level references folder as a non-production archive;
  use only `local-data/production/references/` for production corrections.
  No HA runtime behavior or app release version changes.

## 0.1.33

- Retry unidentified hens with up to 15 extra shots within the round's capture
  timeout, stopping when identities are available. Prefer accepted identities
  over unknown shots independently per visible split region; retain unknown
  when no shot meets the existing appearance thresholds.
- Let unknown presence frames complete an identified arrival's confirmation,
  without overriding known visitors or carrying identities across empty frames.

## 0.1.32

- Make Review & teach confirm edited labels for the training archive by default;
  add an unchecked Add as Reference checkbox for optional reference activation.
- Rename capture navigation to Previous day and Next day.
- Hide egg-count lines for obscured frames and regions.

## 0.1.31

- Use Home Assistant local time for UI timestamps and capture/journal day grouping.
- Put capture day links around the selected day, add photo time headings, and
  remove the capture Refresh link.
- Group Journal cards by day, put time in the heading, remove the Confirmed and
  Center prefixes, and show one full-nest picture above collapsed evidence.
- Show Sleep mode overnight and No events yet after waking until a new event,
  including the MQTT latest-event sensor; preserve journal history and counts.

## 0.1.30

- Ignore small split-presence changes such as loose feathers when their actual
  changed area is below the chicken-area threshold, even if a filled contour
  encloses surrounding unchanged straw. Retain larger partial-body uncertainty.

## 0.1.29

- Treat center as presence-only throughout the UI: no egg-count line in either
  Status section, no center egg field in diagnostics, and no center egg captions
  in Journal evidence. Omit center egg counts from new training annotations.
  Keep side-zone counts and existing stored data unchanged.

## 0.1.28

- Add small Confirm buttons to Status, Captures, Journal evidence, and References.
  Retain the original image and the displayed interpretation in a private training
  archive, independently of capture/journal retention and active references.
- Preserve per-zone predictions, uncertainty, and available egg counts; bind
  confirmations to the displayed image and interpretation, not a newer result.
- Add a configurable training-storage cap and one local pull for captures,
  paired results, and training examples. Optionally remove only checksum-verified
  transferred training files from HA, preserving images used by newer examples.

## 0.1.27

- Assign each detected body to the side or center rectangle with the greatest
  body-mask overlap, independently of appearance-based identity recognition.
- Transfer a known hen's confirmed position after three confirming observations;
  record one “Hen_a moved to center” event instead of leaving her in two zones
  and later reporting two departures. Apply the same rule on both split axes.
- Commit multi-zone decisions with restart-safe replay. Keep obscured former
  positions uncertain, retain clean egg totals, and discard unsafe attribution
  across moves. Do not rewrite existing journal entries or egg totals.

## 0.1.26

- Remove explanatory UI filler and disclaimers from Status, Captures, Journal,
  References, and review feedback. Keep actionable warnings and concise labels.
- Add a project rule against explanatory UI boilerplate.

## 0.1.25

- Count eggs independently in each clear side while the other side is occupied
  or locally obscured. Divider occupancy or ambiguity still blocks hidden nests.
- Keep the center strictly presence-only, with no egg counts or laying events.
  Combine side totals by hen identity, regardless of which side she visits.
- Reset partial egg-count confirmation when a hen appears, so pre-visit shots
  cannot help confirm a post-visit egg count.

## 0.1.24

- Collapse each Journal event's details and pictures into an expandable section,
  keeping its title and confirmation time visible. Pause automatic refresh while
  a section is open so reading or reviewing evidence is not interrupted.

## 0.1.22

- Show zones in spatial order in both Status prediction and confirmed-state
  sections: Left, Center, Right or Top, Center, Bottom.
- Omit the egg-count suffix from capture captions when the count is unknown.

## 0.1.21

- Order review regions spatially: Left, Center, Right or Top, Center, Bottom,
  with Whole frame first.

## 0.1.20

- Remove the Review & teach confirmation checkbox and its extra save requirement.
  Selecting a label and pressing Save now records the review directly.

## 0.1.19

- Make capture captions truly compact and frame-only: spatially ordered labels
  separated by bars, followed by one raw frame egg count. Remove confirmed-state
  history and repeated zone headings; unknown counts remain ? rather than zero.
- Add a shared Review & teach popup to Captures and Journal photos: select a
  highlighted region, an existing hen or a new hen, Empty or Obscured. Require
  explicit visual confirmation and save; retain reviewed indicators across pages.
- Validate and atomically activate compatible hen crops and whole-frame empty
  calibration for subsequent recognition. Obscured, partial Empty and incompatible
  layouts remain clearly labelled annotations only. Preserve historical predictions,
  journal entries, images and egg totals.
- Persist content-linked reviews, prevent duplicate reference additions, reject
  stale saves, and allow hash-checked Undo of only references created by that review.
  Keep interrupted activation recoverable. Protect UI-reviewed references from
  being silently discarded by desktop reference replacement.

## 0.1.18

- Normalize reviewed empty and live split views with the same area resampling
  and full-frame split grid, avoiding false background-change occlusion from
  aliased texture or independently resized half views.
- Suppress fine background-edge differences before the conservative per-side
  change check, while measuring body thickness on its filled silhouette so
  neutral mask holes do not hide broad or clipped bodies.
- Detect broad losses of reviewed background texture as possible bodies, even
  when their brightness matches the background. Preserve the feather/smooth-patch
  gate so smooth egg piles do not become visitors through this new cue.
- Reject egg ellipses whose centers fall outside their visible surface hull;
  a plausible size/area ratio alone must not turn a short wall arc into an egg.
- Require a visitor to cover at least ten maximum-egg areas in normalized pixels.
  Smaller convincing bodies/fragments are obscured, not identified visitors.
- Freeze all split egg counting while any zone has a hen or is obscured, keeping
  the previous clean count. Obscured shots pause rather than advance or reset
  confirmation counters. Small fragments leave visit attribution untouched;
  genuinely hidden/ambiguous nests still invalidate unsafe laying attribution.
- Add synthetic regressions for both split axes, minimum visitor size, texture
  changes, resampling, egg-pile gating, skipped shots and journal evidence. Do
  not rewrite historical events or counters.

## 0.1.17

- Show each stored capture's paired image prediction and confirmed state in a
  compact summary, including split-zone states and meaningful egg counts. Mark
  missing or invalid results unavailable rather than guessing from folders.
- Keep just the filename below the summary; remove duplicate timestamps, folder
  paths, file sizes and separate full-size links. Photos remain clickable.

## 0.1.15

- Add a read-only Captures page to HA Ingress with lazy-loaded photos, folder
  labels and full-size links. Show one UTC capture day at a time, defaulting to
  the newest stored day, with older/newer navigation across available days.
- Keep capture storage, deduplication, retention and recognition unchanged.

## 0.1.14

- Show all active reference photos on one References page, grouped by hen with
  empty calibration photos last. Remove paging controls and retain lazy-loaded
  images. Existing reference paging links now show the complete gallery.

## 0.1.13

- Add explicit `push --replace --yes` to validate and atomically replace the
  complete production reference set, including removals and corrected identities.
  Preview counts with `--replace --dry-run`; verify remote paths and checksums
  after replacement. Failed validation or stale uploads leave references unchanged.
- Store one flat reference folder, with no retained versions or rollback API/CLI.
  Migrate the former active version on startup, preserving its images and removing
  version history. Use temporary staging and Linux atomic directory exchange;
  content checksums remain only for concurrency and cache invalidation.

## 0.1.12

- Make Status the default HA Ingress page and add a shared MunAI sidebar with
  Status, Journal and References navigation (compact navigation on small screens).
- Show the latest successfully processed capture paired with its own prediction,
  separate confirmed per-zone state, uncertainty explanations and diagnostics.
  Refresh automatically; show capture/MQTT health, failures, age and stale warnings.
- Keep at most two latest images in memory even when ordinary photo storage is
  disabled or deduplicated. Timestamp-pinned URLs never substitute another image;
  restart waits for a new successful observation. No recognition or counting changes.

## 0.1.11

- Add a read-only References page linked from the HA Ingress journal, showing
  the server's active reference version, configured split mode, per-hen counts,
  paginated identity photos and separate full-frame empty calibration images.
  Show filenames and full-size original links; exclude staging, previous versions,
  local candidates and unreviewed predictions.
- Pin reference image URLs to their active version. If references change while
  a page is open, require a refresh instead of silently substituting a different
  photo. Retain Ingress-only access and the journal's security headers. Gallery
  metadata does not hash or decode all reference photos on each page load.
- Keep reference editing and journal-to-reference promotion out of this release;
  no labels, recognition settings, reference activation or historical totals change.

## 0.1.10

- Deduplicate ordinary HA photo storage after recognition, while retaining raw
  label/count changes, split-zone changes and a configurable 15-minute heartbeat.
  Compare with the last successfully saved frame; suppress small detail noise
  without camera-specific exclusion coordinates. Failed comparisons retain photos.
  Every observation and actual journal confirmation frame remains processed.
- Restore the 1,000-photo ceiling, separate from the 10,000-result JSON ceiling.
  Keep age, combined byte budget and disk reserve protections; photo deduplication
  does not guarantee full-day photo coverage during continuous activity.
- Add opt-in vertical (left/right) and horizontal (top/bottom) split recognition,
  with shared half-frame appearance references and separate divider presence.
  Preserve measurement scale; retain full-frame reviewed empty calibration and
  accept reference filename view suffixes as provenance, never identity features.
- Track each nest's visits, egg baselines, confirmation and daily production
  separately. Divider/ambiguous observations freeze hidden nest counts and
  invalidate unsafe attribution. Establish fresh split baselines without
  recounting existing eggs; preserve the whole-box daily totals as a dated carry.
  Fail closed on incompatible layout changes instead of mapping old baselines.
- Journal split events with explicit zones, zone-specific confirmation evidence,
  highlighted views, visible stable photo IDs and shared full-frame photos. Recover each region's durable
  outbox idempotently; retain active evidence from every region independently of
  ordinary photo rotation. Discover split occupant/count MQTT sensors.
- Use reviewed empty-background change checks conservatively to pause counting
  for patterned/occluding bodies missed by the smooth-region heuristic. Unknown
  and obscured remain valid outcomes; new bedding/lighting can require refreshed
  empty calibration. This remains an experimental recognition pilot.

## 0.1.9

- Raise the combined capture/result file ceiling from 1,000 to 10,000, and align
  transfer listing/client limits so a full day of minute-by-minute observations
  can be retained and exported when the byte budget and retention age allow.
- Increase the new-install capture storage default from 256 to 2,048 MiB. Existing
  configured limits remain unchanged; increase them explicitly in HA. Age, byte
  and disk reserve limits still apply; 24-hour coverage is not guaranteed on a
  full disk or with unusually large images.
- Add read-only `remote history` coverage/size summaries to the transfer CLI.
- Add an offline-only split-box presence prototype and visual review tool. It
  detects candidate side/divider occupancy and flags conflicting/merged bodies;
  it does not yet change HA recognition, references, MQTT, events or egg counts.

## 0.1.8

- Require five consecutive chicken-free observations to confirm departure; retain
  three-frame confirmation for arrivals, identity changes and matching egg counts.
- Retain the last confirmed visitor and pre-visit baseline after a settled count,
  so a missed egg can produce a later laying event and credit that visitor's daily
  total. Preserve attribution through restarts; replace it on a new confirmed visit
  and end it on confirmed collection. Never invent a missing baseline or recover
  identity from historical event text.
- Persist the highest confirmed egg count until confirmed zero collection so a
  partial detection dip/rebound cannot count the same eggs twice. Partial collection
  remains unsupported; new production must exceed that high-water count.
- Preserve all five departure photos and later matching-count photos in journal
  evidence, including delayed laying after an initially settled zero count.

## 0.1.7

- Add a read-only, newest-first journal through authenticated HA Ingress, with
  paginated events, local timestamps, lazy-loaded full-size photos and evidence
  captions. No extra host port, transfer token or public /local files required.
- Archive the actual three-frame confirmation sequences, before-visit counts,
  confirmed identities and delayed post-departure counts independently of normal
  capture storage. Preserve active visit evidence across restarts; never promote
  predictions to reviewed references.
- Commit journal events through a durable nest-state outbox with idempotent crash
  recovery. Migrate existing nest state without inventing historical evidence.
- Default journal retention to 365 days, separately configurable from capture
  rotation. Share archived photos, discard superseded pending frames promptly,
  and show archive usage/low-storage warnings without silently shortening retention.

## 0.1.6

- Add a dated, retained daily egg-count MQTT object for Influx bridges, with
  atomically persisted per-hen totals and an unknown bucket for every unattributed
  confirmed increase. Reset on the service's local day, independently of capture
  pauses; preserve visit state and safely migrate previous persistence formats.
- Discover Home Assistant daily total and per-hen/unknown production sensors with
  date attributes for ordinary Entities/Tile cards.
- Publish confirmed local state consistently to MQTT, live status, results and
  occupant logs. Freeze egg counts while occupied; require three matching empty
  counts for every update, and report unknown until a count is established.
- Separate departure from egg-count confirmation, preserving pending laying
  attribution across restarts. Migrate existing persisted visit state safely.

## 0.1.5

- Add a retained Latest event MQTT sensor for confirmed arrivals, departures,
  laying events, and egg collection. Require three consecutive observations for
  presence/identity changes and chicken-free zero counts for collection.
- Preserve known identities through unknown frames; freeze the pre-visit egg
  baseline and avoid egg attribution when hens swap without confirmed absence.
- Atomically persist validated visit state and the latest event across restarts;
  restart confirmation streaks and fail closed on corrupt/unsupported state.

- Reject egg-pile chicken false positives using unclosed smooth-patch thickness
  together with plumage texture; share the presence check across recognition and
  pre-sorting. Check smaller candidates after rejecting a larger pile, and keep
  hen priority over all egg processing.
- Add 24 committed, reproducibly drawn offline image fixtures for empty nests,
  separated eggs, touching piles, smooth/patterned/clipped hens, and mixed scenes;
  exercise recognition, pre-sorting, counting gates, and HA prediction together.

## 0.1.4

- Discover an Egg count MQTT measurement sensor alongside Occupant using the
  offline OpenCV detector. Report zero for known/unknown hens without inspecting
  eggs; count visible eggs only when no hen is detected.
- Share non-retained state, availability, expiry and rediscovery between both
  sensors; include egg counts in stored observations and transfer status.
- Expose minimum/maximum egg area settings for private deployment calibration.
  Default the minimum area to 950 in HA and offline counting; size settings remain
  configurable and require checks against reviewed positive and negative images.
- Keep full image frames visible in offline reports and tolerate straw notches,
  touching eggs and mostly visible clipped eggs while rejecting textured regions.

## 0.1.3

- Derive recognition background exclusions from reviewed empty-nest references,
  including egg-only images; remove manual mask fields and local profile reads
  from recognition. No persistent calibration cache.
- Transfer/version reviewed `empty/` references separately from hen identities;
  require empty references before recognition and never promote predictions.

## 0.1.2

- Simplify transfers: standard-library devcontainer CLI with an explicitly
  approved gitignored owner-only token file. Remove native Keychain dependency,
  separate CLI requirements, and the proposed automatic-venv wrapper.
- Resume interrupted offline reviews from their existing manifest; skip completed
  relocations/results and pace the single worker to a default 10% CPU budget.
- Fix HA configuration: replace the unusable required boundary list with numeric
  Left boundary (0) and Right boundary (640) fields.
- Make empty exclusion/additional-client lists genuinely optional; use text rows
  instead of the UI-optional/backend-required list-of-object mismatch.
- Add simple primary transfer-client fields; pairing needs only a pasted digest.
- Keep legacy runtime options readable and reject conflicting masks safely.

## 0.1.1

- Share the image layout between development and HA: separate versioned
  references, flat capture categories, and named `unreviewed/` predictions.
- Resolve the same active reference snapshots in the native CLI and Pi scripts.
- Preserve standalone/Pi cron workflows with pending/unknown/empty/egg categories.
- Add dry-run-first local layout migration and reviewed-reference import tooling.
- Add an offline review runner using the HA image pipeline, checksum verification,
  self-excluded reference checks and untouched golden references; no retention.
- No automatic migration, batch classification, publication, or live deployment.

## 0.1.0

- Experimental locally built Home Assistant app with standard configuration UI.
- Serial bounded capture/recognition loop; legacy Pi/cron option retained.
- MQTT device discovery, Occupant sensor, availability and observation expiry.
- Pinned HTTPS transfers and native macOS CLI with Keychain token storage.
- Validated, versioned references; checksums, explicit overwrite and rollback.
- Bounded capture/result storage and change-oriented, sanitized logs.
- Synthetic offline tests and ARM64/AMD64 build-check workflow. Live deployment,
  native Keychain and Green performance validation remain pending.
