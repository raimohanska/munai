# Home Assistant Green deployment handoff

## Scope and boundaries

Develop the application, Home Assistant add-on/app packaging, file-transfer API,
and dev-machine CLI inside the existing devcontainer. This is a plan, not
authorization to deploy: the Green is critical. Do not connect to or modify the
Green, Pi, camera, or broker without explicit approval. Do not install host tools.
Read `AGENTS.md` first; never inspect, copy, source, or expose `.env`.

The workspace already contains capture, recognition, MQTT code, and tests,
including uncommitted work. Inspect and reuse it without overwriting user work.
`PLAN.md` is an older image-experiment handoff, not current implementation status.
Retain capture and recognition modules imported by HA. The old standalone
Pi/cron MQTT runner and local migration/import/batch-review utilities are removed.

## Intended architecture

- Versioned application image, built locally on the Green like Paine; CI checks
  ARM64 and AMD64 builds. A prebuilt container registry remains optional.
  Include FFmpeg, a supported pinned Python, NumPy, OpenCV, and MQTT support.
  Validate dependency availability and memory use before choosing versions.
- A Home Assistant add-on manages startup, logs, configuration, and persistent
  `/data`. Run one bounded capture/recognition round at a time on an internal
  schedule, reusing the existing program rather than rewriting recognition.
- Keep credentials in local add-on configuration, never Git or image layers.
  Provide an explicit configuration adapter; do not package the developer `.env`.
- Store reviewed references, optional models, runtime configuration, captures,
  and unreviewed predictions separately under `/data`. Application updates must
  preserve them. Document uninstall behavior and backup/restore.
- Browser management can later use HA ingress. CLI transfers need a separate,
  narrowly scoped HTTPS API; do not assume ingress accepts application tokens.
  Do not require a main HA password or a broad HA long-lived access token.

## Implementation order

1. **Inspect and design.** Read current code and tests. Check current official HA
   add-on documentation. Propose the data layout, configuration adapter, API,
   container permissions, and concrete HTTPS/certificate setup. Resolve TLS
   termination before enabling remote transfers; never send bearer tokens over
   plain HTTP. Keep the endpoint LAN/VPN-only, with no router port forwarding.
2. **Offline service skeleton.** Add status and persistent-data management around
   existing recognition. Start with synthetic images and dummy configuration;
   no live capture or broker connections. Add bounded scheduling, clean shutdown,
   sanitized logging, capture retention, and disk-space safeguards.
3. **Transfer API.** Support listing and downloading captures/results, uploading
   references/configuration/models, and exporting valuable data. Use revocable,
   application-specific random tokens, read-only/read-write scopes, and hashed
   server-side token storage. Document local token provisioning and rotation;
   never expose an unauthenticated remote bootstrap endpoint.
4. **Safe file activation.** Constrain paths to allowed data roots; reject path
   traversal and symlink escapes. Bound uploads, decoded image sizes, and disk
   consumption. Stage and validate changes, then activate atomically between
   jobs, retaining the previous known-good data version. No arbitrary shell/code
   execution, pickle/joblib loading, or unrestricted archive extraction. Start
   with individual files and manifests/checksums rather than archive uploads.
5. **CLI.** Implement commands such as `remote login/status/logout`, `push`,
   `pull`, and export, with dry-run, checksums, explicit overwrite behavior, and
    no deletion by default. Per the later user decision, run the standard-library
    CLI in the devcontainer and keep its token/address/fingerprint in ignored
    `.munai-remote.json` with owner-only permissions. Never use token command-line
    arguments or logs. Local logout is distinct from server-side revocation.
6. **Package and document.** Add minimal add-on metadata, repository metadata,
   container build, and CI for versioned ARM64 images. Keep datasets, credentials,
   and models out of build contexts using explicit allowlists. Public metadata
   and images are simplest if the user accepts publishing code; otherwise first
   validate private Git and registry authentication, including updates. Do not
   assume private repositories are seamless or embed credentials in tracked URLs.
7. **Approval gate.** Present local results, resource measurements, permission
   list, TLS setup, backup/restore procedure, and rollback plan. Only with explicit
   approval perform a small manual Green pilot, initially with capture disabled,
   then enable a conservative schedule and monitor Home Assistant responsiveness.

## Devcontainer limitations and credentials

- Application/API/CLI development and offline tests fit in the devcontainer.
  It currently has no Docker socket integration; nested image builds and a full
  Supervisor test environment need additional, explicitly approved tooling or CI.
  Do not silently mount the host Docker socket or enable privileged mode.
- The user explicitly replaced native Keychain/venv tooling with a devcontainer
  CLI and a plaintext local credential file, analogous to .env. Protect that file
  with mode 0600; never commit, inspect via agent tools, log, or package it. Tests
  use synthetic temporary credentials. No host tooling or Keychain access is needed.
- ARM64 runtime behavior, ingress, Supervisor
  permissions, and Green resource usage need validation outside ordinary unit
  tests. Clearly distinguish simulated tests from hardware validation.

## Safety and acceptance checks

- No privileged mode, Docker socket, host mounts/networking, devices, or HA API
  permissions by default. Keep protection mode enabled and use least privilege.
  Verify actual CPU/memory limits supported by HA; do not assume limits exist.
  Training and continuous video processing stay off the Green.
- Preserve appearance-based identities and an unknown result. Ignore eggs when
  a hen is present. Named predictions remain in `unreviewed/`; never promote them
  automatically. Exclude reserved categories and nested predictions from references.
- Test API authorization, malicious paths, upload limits, partial transfers,
   atomic whole-set replacement, retention, redaction, and CLI behavior using local
  fixtures. Follow `AGENTS.md`: no automated camera-capture tests.
- Demonstrate upload/download round trips and restart persistence locally with
  dummy credentials. Verify unauthorized access fails and secrets are not logged.
- Document bounded storage, backups of reviewed data, token revocation, manual
  updates, and image/data rollback. A container shares Green CPU/RAM/storage;
  isolation cannot guarantee Home Assistant availability.

First milestone: an offline local API + CLI round trip with dummy files and
credentials, plus add-on packaging skeleton. Defer a full browser UI until this
works. Do not deploy as part of that milestone.

## Confirmed decisions and implementation notes

- Publish this sanitized repository publicly; do not create a separate app repo
  or publish the unsanitized recovery bundle/history. No registry is required.
- Keep all real identities, images, models and deployment calibration out of Git.
  Tests generate synthetic images and use synthetic identities.
- HA is the supported runtime; the standalone cron/Pi runner has been retired.
  The app uses a serial Python loop, with capture and MQTT disabled on first installation.
- Configure runtime settings in HA's standard app form, not .env or uploaded code.
- Use MQTT discovery for one stable device and an Occupant sensor. States are a
  configured identity, empty, unknown, or HA unavailable for stale observations.
  Egg count and other sensors remain later work.
- The devcontainer CLI stores its token in ignored owner-only `.munai-remote.json`;
  HA stores only random-token SHA256 digests. No wrapper or CLI dependencies.
  HTTPS terminates inside the app using a persistent generated certificate. Pair
  its exact public fingerprint from trusted HA logs before sending any token.
  No host SSL mount or HA/Supervisor API permission is needed.
- Support individually validated JPEG transfers and explicit atomic whole-set
  replacement with checksum conflicts and confirmation of remote removals.
  Stage the complete set and atomically exchange directories between rounds;
  retain no reference version history or rollback target.
- Export references/captures/results. Defer arbitrary configuration/model uploads:
  runtime configuration belongs in HA's form, and executable model formats are
  neither needed nor accepted. Back up private configuration/TLS identity via HA.
- Logs show changes, actionable failures and recovery; identical failures are
  suppressed for 15 minutes. Never log secrets or raw external exceptions.
- Use the same relative data layout locally and on HA. Reviewed references live
  directly in `references/<identity>/`, with no version history or state pointer.
  The canonical editable deployment source is `local-data/production/references/`.
  Captures use `empty/`, `unknown/`, and `<identity>/unreviewed/` directly below
  `captures/`; standalone/offline tools also use `pending/` and optionally `eggs/`.
  Migrate existing data explicitly without reading .env or contacting hardware.
- Keep local reviewed originals and split-diagnostic datasets outside HA retention.
  The root-level `references/` is a non-production archive of images absent from
  canonical at cleanup time, not another production reference source.

See DOCS.md for executable setup instructions, security limits, and the distinction
between local tests, configured CI, and pending Green validation. Developing
the app is not permission to publish, connect to hardware, or deploy it.
