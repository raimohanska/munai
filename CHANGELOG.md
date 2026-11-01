# Changelog

## 0.1.1

- Share the image layout between development and HA: separate versioned
  references, flat capture categories, and named `unreviewed/` predictions.
- Resolve the same active reference snapshots in the native CLI and Pi scripts.
- Preserve standalone/Pi cron workflows with pending/unknown/empty/egg categories.
- Add dry-run-first local layout migration and reviewed-reference import tooling.
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
