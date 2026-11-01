# Munai: install, configure, and transfer references

Munai is an **experimental Home Assistant app**. It builds locally on the Green,
like Paine; no container registry is required. The repository root is the app's
build context. Supervisor discovers the root `config.json` and `repository.json`.
Docker's explicit allowlist excludes Git history, `.env`, references, and local
camera calibration. The legacy Pi/cron entry points remain available unchanged
in usage; do not retire them until the HA pilot works.

## Validation status

Offline tests exercise real local HTTPS, synthetic reference upload/download,
authorization, pinning, rollback, persistence, retention, MQTT payloads, and CLI
behavior with an in-memory credential store. They do not access real images,
`.env`, cameras, brokers, HA, or native Keychain. CI builds both ARM64 and AMD64
images and smoke-tests them without networking. **A configured workflow is not
evidence of a successful CI run.** Supervisor installation, native macOS Keychain,
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

## 2. Pair the native macOS CLI

Run these commands in a **native macOS terminal**, from this checkout, not inside
the devcontainer. Use Python 3.10+; the CLI does not need OpenCV or FFmpeg. The
agent does not install anything on your host.

```bash
python3 -m venv ~/.venvs/munai-remote
~/.venvs/munai-remote/bin/python -m pip install -r requirements-cli.txt
~/.venvs/munai-remote/bin/python munai_remote.py remote pair https://homeassistant.local:8443 --fingerprint FINGERPRINT_FROM_HA_LOGS
```

Pairing checks the server certificate **before sending any HTTP headers**,
generates a random 256-bit token, and saves it directly in macOS Keychain. Only
the token's **SHA256 digest** is printed. The raw token is never printed, put in
process arguments, or saved in the profile JSON. The profile contains only the
address, public certificate fingerprint, and a Keychain account identifier.

In HA's **Configuration** form, use the ordinary primary-client fields:

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
~/.venvs/munai-remote/bin/python munai_remote.py remote status
```

It should show zero references and capture/MQTT disabled. There is no plaintext
token-file fallback. Keychain must be available and unlocked. Profile defaults
are `green` and `~/.config/munai/profiles.json`; use global options **before** the
command to select another profile or non-secret profile file:

```bash
~/.venvs/munai-remote/bin/python munai_remote.py --profile second-nest remote status
```

The public certificate and its private key persist under `/data/tls`. Trust uses
the exact certificate fingerprint instead of public CA/hostname validation. If
it changes, the CLI refuses to send the token. Investigate through HA's trusted
logs; do not accept a new fingerprint supplied by an untrusted network endpoint.

## 3. Configure recognition and upload reviewed references

Before uploading, set any camera-specific masks in HA's **Configuration** form:

- **Left boundary** (`horizontal_left`): number, default **0**.
- **Right boundary** (`horizontal_right`): number, default **640**.
- **Camera overlay/wall exclusions** (`excluded_rectangles`): optional; leave
  empty for none, or add a text row `x1,y1,x2,y2` per rectangle, without brackets.

Coordinates use the **640×360 processing frame**, not the original camera
resolution. They exclude overlays/walls only, never chicken identities. Your
ignored local `camera-profile.json` uses rectangles as `[x1, y1, x2, y2]` arrays;
copy its left/right values into the two numeric HA fields, and its rectangles as
comma-separated text rows. No brackets or JSON are typed into HA's form. No real camera
calibration is included in this repository. Save and restart after option changes.

The same relative layout is used on this machine (the checkout is the data root)
and HA (the data root is `/data`):

```text
<data-root>/
  references/
    state.json                         # active and previous version IDs
    versions/<version>/
      hen_a/20000101T120000_000000Z.jpg
      hen_b/20000101T120100_000000Z.jpg
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
inside identity folders qualify. Nested predictions never become references.

```bash
~/.venvs/munai-remote/bin/python munai_remote.py push references --dry-run
~/.venvs/munai-remote/bin/python munai_remote.py push references
~/.venvs/munai-remote/bin/python munai_remote.py remote status
```

The server validates each JPEG and checks it contains a measurable hen using
the configured masks/thresholds. It checks SHA256, rejects symlinks and unsafe
paths, stages the change, and atomically activates it **between recognition
rounds**. Readiness requires reviewed references for at least **two identities**.
Include varied appearances/poses; missing nest positions are never identity evidence.

`push references` resolves only the active local snapshot from `state.json`; it
does not upload previous versions, staging files, or any captured predictions.
A flat directory of explicitly reviewed identity folders can also be used as an
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
as `local-data/reviewed/<identity>/`, then run:

```bash
/tmp/opencode/munai-ha-venv/bin/python tools/import_references.py local-data/reviewed
/tmp/opencode/munai-ha-venv/bin/python tools/import_references.py local-data/reviewed --apply
```

This preserves source images, validates them, and activates local reference versions
between locked updates. Different contents under an existing name require explicit
`--overwrite`. Then run the native `push references` command. Never modify the
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
/tmp/opencode/munai-ha-venv/bin/python tools/review_images.py
/tmp/opencode/munai-ha-venv/bin/python tools/review_images.py --apply
```

The first command predicts without relocating images. `--apply` sorts all managed
captures into `empty/`, `unknown/`, or `<identity>/unreviewed/`, preserving filenames
and verifying checksums. This uses the local camera mask and the HA default
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

Use an ignored `local-data/` destination for exports:

```bash
~/.venvs/munai-remote/bin/python munai_remote.py pull references local-data/reviewed-backup
~/.venvs/munai-remote/bin/python munai_remote.py pull captures local-data/captures --dry-run
~/.venvs/munai-remote/bin/python munai_remote.py export local-data/export
```

Downloads verify checksums and activate atomically. Different existing local files
require `--overwrite`; no local files are deleted. A download during reference
changes or capture retention may fail safely; rerun it. For a consistent full
backup, disable capture and avoid uploads, or use HA's cold app backup.

```bash
~/.venvs/munai-remote/bin/python munai_remote.py rollback --yes
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
- If the new app causes trouble, stop it and restore the old cron schedule. Keep
  the legacy Pi references/configuration and ignored camera profile backed up.

## Logs and troubleshooting

### Upgrading the configuration form from 0.1.1

Version 0.1.2 replaces the broken required numeric-list `horizontal_bounds` field
with **Left boundary = 0** and **Right boundary = 640**. Empty exclusions and extra
clients are now genuinely optional to both HA's UI and Supervisor validator.
Reload the app repository, update Munai, then reopen Configuration. Do not reset
your camera or MQTT settings. Copy any custom old boundary values into the new
numeric fields before enabling capture. Saving the new form removes the obsolete
schema key. The runtime accepts old bounds when the new fields are absent and
rejects conflicting old/new masks rather than silently changing calibration.

For old nonempty exclusion lists, re-enter each rectangle as `x1,y1,x2,y2`.
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
- **Reference rejected:** inspect the JPEG, filename, masks, and recognition
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
