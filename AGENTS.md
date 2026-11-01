# Project instructions

## Workflow

- Commit completed work when it is ready, after running applicable checks.
- Include only files related to the task; do not commit unrelated user changes,
  secrets, or generated captures. Do not push unless explicitly requested.

## Goal

Collect timestamped images from an overhead LAN RTSP camera, then build a
dataset of egg-laying events and identify which of the configured hens
produced each egg.

## Raspberry Pi compatibility

- Target Python 3.5.3 and Node.js 12.13.1.
- Implement the initial capture script in Python using the standard library
  and the system FFmpeg executable.
- Keep syntax, APIs, and dependencies compatible with the target hardware.
- The capture script runs once per invocation; cron handles scheduling.
- Use UTC timestamps in image filenames.

## Configuration and secrets

- Read RTSP configuration and credentials from a local `.env` file at runtime.
- Never read, inspect, print, copy, or expose the user's `.env` file through
  agent tools, shell commands, logs, or responses.
- Do not source or execute the `.env` file during agent work.
- Do not log credentials or credential-bearing RTSP URLs. Sanitize error
  output from external programs before reporting it.
- Never commit `.env`. Use placeholder values only in documentation or an
  eventual `.env.example`.
- Do not install a dotenv dependency that requires a newer Python version;
  prefer a small, explicitly documented standard-library parser.

## Capture reliability

- Prefer RTSP over TCP initially.
- Bound capture time and prevent overlapping cron invocations.
- Write to a temporary image file and rename only after successful capture.
- Use absolute paths and return a nonzero exit status on capture failure.

## Testing

- Do not write automated tests for camera capture functionality.

## Chicken recognition

- Use appearance, not nest position, to identify Hen_a, Hen_b, and Hen_c.
  Reference images do not necessarily cover
  every hen in every position. Never infer identity from that missing coverage.
- Coordinates, nest zones, and body orientation must not be identity features.
  Camera-specific masks may exclude text/walls, but must not assign identities.
- Prefer position-independent brightness/texture summaries; changed poses can
  still obscure markings, so retain an unknown outcome for ambiguous images.
- Ignore eggs when a chicken is present.
- Automatically saved identity predictions must not become reference images
  without review. Keep them in named `unreviewed/` subdirectories; reserve
  `chicken/unknown/` for unidentified hens and exclude it from reference loading.
