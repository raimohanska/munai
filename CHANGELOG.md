# Changelog

## Unreleased

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
