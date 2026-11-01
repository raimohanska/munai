# Munai: install, configure, and transfer references

For the step-by-step setup and what to do next, start with [README.md](README.md).
This file covers advanced settings, storage, offline tooling, and validation details.

Munai is an **experimental Home Assistant app**. It builds locally on the Green,
like Paine; no container registry is required. The repository root is the app's
build context. Supervisor discovers the root `config.json` and `repository.json`.
Docker's explicit allowlist excludes Git history, `.env`, references, and local
camera calibration. The legacy Pi/cron entry points remain available unchanged
in usage; do not retire them until the HA pilot works.

## Validation status

Offline tests exercise real local HTTPS, synthetic reference upload/download,
authorization, pinning, rollback, persistence, retention, MQTT payloads, and CLI
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

The same relative layout is used on this machine (the checkout is the data root)
and HA (the data root is `/data`):

```text
<data-root>/
  references/
    state.json                         # active and previous version IDs
    versions/<version>/
      hen_a/20000101T120000_000000Z.jpg
      hen_b/20000101T120100_000000Z.jpg
      empty/20000101T120200_000000Z.jpg  # reviewed no-hen background, eggs allowed
    staging/
  captures/
    pending/                           # unclassified standalone/offline frames
    empty/
    eggs/                              # optional legacy/offline egg candidates
    unknown/                           # detected hen, identity not known
    hen_a/unreviewed/                   # named automatic predictions
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

`push references` resolves only the active local snapshot from `state.json`; it
does not upload previous versions, staging files, or any captured predictions.
A flat directory of explicitly reviewed identity and `empty/` folders can also be used as an
import source, but is not the managed data layout.

### Migrate existing local images before deployment

In the devcontainer, preview the old layout migration, then explicitly apply it:

```bash
/tmp/opencode/munai-ha-venv/bin/python tools/migrate_layout.py
/tmp/opencode/munai-ha-venv/bin/python tools/migrate_layout.py --apply
```

It imports reviewed images from the old named `captures/chicken/` folders into
validated versioned references, moves raw root JPEGs into `captures/pending/`,
flattens unknown/predicted captures, and preserves CSV manifests under
`reports/legacy/`. Existing empty/egg captures are unchanged. It checks conflicts
before moving anything, never overwrites differing files, verifies every reference
before removing its original name, and can be rerun after interruption. It takes
the local capture/device/service/sorter locks; stop those writers first. It never
reads or changes `.env`, contacts a camera/broker/HA, or runs batch classification.
Existing offline reports retain their historical paths; regenerate rather than
editing them. Take a private data backup before a large manual reorganization.

To add newly reviewed images later, put copies in an ignored staging folder such
as `local-data/reviewed/<identity>/` or `local-data/reviewed/empty/`, then run:

```bash
/tmp/opencode/munai-ha-venv/bin/python tools/import_references.py local-data/reviewed
/tmp/opencode/munai-ha-venv/bin/python tools/import_references.py local-data/reviewed --apply
```

This preserves source images, validates them, and activates local reference versions
between locked updates. Different contents under an existing name require explicit
`--overwrite`. Then run the `push references` command above. Never modify the
immutable files under `versions/` directly, and never import predictions without
review. A reference-only CLI backup/pull is a **flat import folder**; restore it
using this importer, not by downloading into a live managed `references/` root.

Unchanged files are skipped. No files are deleted. Different contents under an
existing name require `--overwrite`; inspect the conflict before using it. Each
file is an atomic change, **not an all-or-nothing batch**: an interrupted push may
have activated earlier files. Rerun it safely to finish. Concurrent changes cause
a version conflict instead of overwriting another upload.

Limits: **8 MiB per JPEG, 16 million decoded pixels, 8192 per dimension, 500
references and 128 MiB per reference set**. The current and previous reference
versions are preserved, with immutable shared files. An interrupted unactivated
version is cleaned at the next startup. Keep originals on this machine too.

## 4. Configure MQTT, then capture

1. Set `mqtt_host` (default `core-mosquitto`), `mqtt_port`, `mqtt_user`,
   `mqtt_password`, and `mqtt_tls` in the HA form. TLS uses system CA trust and
   hostname validation, with no insecure mode. Plain MQTT is unencrypted: restrict
   it to a trusted network. No Supervisor MQTT credential lookup is required.
2. Keep a stable `device_id` and choose `device_name`. The default ID
   `munai_ha_nest` differs from the legacy cron device to avoid discovery conflicts.
   Enable `mqtt_enabled`, save and restart. The HA MQTT integration should discover
   the device and its **Occupant** sensor, initially unavailable.
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

The broker must permit publishing to:

```text
homeassistant/sensor/<device_id>/occupant/config
munai/<device_id>/state
munai/<device_id>/availability
```

and subscribing to `homeassistant/status`. Additional sensors, including egg
count, are deferred. Eggs are ignored when identifying occupants.

One Python scheduling loop runs serial rounds, uses monotonic deadlines, skips
missed rounds instead of catching up, and waits interruptibly on shutdown.
An in-progress FFmpeg call may finish or hit its bounded timeout before shutdown.
Reference features are cached until the active version changes. OpenCV/BLAS use
one worker thread. Training and continuous video processing are not included.

## Storage, downloads, and rollback

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
These retention policies run in the HA service, not the local migration/import or
offline review tools. Local originals are not purged as part of preparing a review.
Hidden local locks/OS metadata are neither exported nor counted as image data.

### Offline review of existing images

After deployment preparation, use the same recognition pipeline without live
capture, MQTT, HA, credentials, training, or retention:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 19 /tmp/opencode/munai-ha-venv/bin/python tools/review_images.py --cpu-percent 10
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 19 /tmp/opencode/munai-ha-venv/bin/python tools/review_images.py --apply --cpu-percent 10
```

The first command predicts without relocating images. `--apply` sorts all managed
captures into `empty/`, `unknown/`, or `<identity>/unreviewed/`, preserving filenames
and verifying checksums. This uses reference-derived calibration and the HA default
recognition thresholds. There is no separate offline identity algorithm.

Active reference originals are never moved or modified. Their review copies are
compared with byte-identical references excluded; singleton identities that cannot
be checked independently remain unknown. Those copies are independent files,
not hardlinks to golden references. The private `reports/review-*/manifest.json`
records source/destination, prediction, checksum, and whether a result is a
reference self-check. `summary.json` marks a completed pass. Existing result JSON
is backed up in that run's report before replacement. Predictions and correlated
frames are **not** a held-out accuracy measurement and never become new references
automatically. Do not run HA's retention loop against this local review dataset.

One process/worker is used. CPU pacing defaults to 10% of one core on average,
with sleeps between operations; short operations can still burst, so this is not
an OS hard CPU quota. Low process priority and one OpenCV/BLAS worker keep the
machine responsive. Run no other image-processing job concurrently.

After interruption, resume the **existing** report instead of starting a second
pass or treating reference review copies as new input:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 19 /tmp/opencode/munai-ha-venv/bin/python tools/review_images.py --apply --cpu-percent 10 --resume reports/review-RUN_ID
```

Resume checks the saved reference version, recognition settings, allowed paths,
and every checksum. It accepts originals already safely relocated, never repeats
prediction, and skips rewriting identical results. Stale predictions or differing
destination contents fail without overwriting anything.

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

```bash
python munai_remote.py rollback --yes
```

Rollback switches to the version before the **last uploaded file**, not necessarily
the beginning of an entire push. It can be reversed by running rollback again.
Export reviewed references before major changes. There is deliberately no remote
delete, arbitrary file/configuration upload, archive extraction or executable
model loading. Runtime settings belong in HA's form; model transfer is deferred
because this recognizer uses references, not trained models.

## Backups, updates, and revocation

- Include Munai in an encrypted **HA backup before updates or uninstalling**.
  Backups are cold: HA stops the app to capture consistent `/data`. They include
  reviewed versions, optional captures, results, TLS keys, and private options.
- Updates rebuild the app locally and preserve `/data`. To roll back code, restore
  the known-good app backup; reference rollback does not roll back code or options.
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
