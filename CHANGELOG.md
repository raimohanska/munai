# Changelog

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
