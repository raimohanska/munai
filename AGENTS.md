# Project instructions

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
