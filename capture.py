#!/usr/bin/env python3
"""Capture one RTSP frame using Python 3.5+ and system FFmpeg."""

import argparse
import datetime
import errno
import fcntl
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMPARISON_WIDTH = 320
COMPARISON_HEIGHT = 180


class CaptureError(Exception):
    """An error whose message is safe to report without revealing secrets."""


def load_env(path):
    """Read literal KEY=value pairs; never execute or interpolate values."""
    values = {}
    try:
        with open(path, 'r', encoding='utf-8') as source:
            for number, line in enumerate(source, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('export '):
                    line = line[7:].lstrip()
                key, separator, value = line.partition('=')
                key = key.strip()
                if not separator or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                    raise CaptureError('Invalid configuration at line {0}.'.format(number))
                value = value.strip()
                if value.startswith(('"', "'")):
                    if len(value) < 2 or value[-1] != value[0]:
                        raise CaptureError('Unclosed quote at line {0}.'.format(number))
                    value = value[1:-1]
                if '\x00' in value:
                    raise CaptureError('Invalid configuration at line {0}.'.format(number))
                values[key] = value
    except (OSError, UnicodeError):
        raise CaptureError('Cannot read configuration file.')
    return values


def configuration(path):
    values = load_env(path)
    url = values.get('RTSP_URL', '')
    if not url.lower().startswith(('rtsp://', 'rtsps://')):
        raise CaptureError('RTSP_URL must specify an RTSP camera URL.')
    try:
        timeout = float(values.get('CAPTURE_TIMEOUT_SECONDS', '30'))
    except ValueError:
        raise CaptureError('CAPTURE_TIMEOUT_SECONDS must be a positive number.')
    if not math.isfinite(timeout) or timeout <= 0:
        raise CaptureError('CAPTURE_TIMEOUT_SECONDS must be a positive number.')
    try:
        pixel_threshold = int(values.get('SIMILARITY_PIXEL_THRESHOLD', '15'))
    except ValueError:
        raise CaptureError('SIMILARITY_PIXEL_THRESHOLD must be an integer from 0 to 255.')
    if not 0 <= pixel_threshold <= 255:
        raise CaptureError('SIMILARITY_PIXEL_THRESHOLD must be an integer from 0 to 255.')
    try:
        min_changed = float(values.get('SIMILARITY_MIN_CHANGED_PERCENT', '0.1'))
    except ValueError:
        raise CaptureError('SIMILARITY_MIN_CHANGED_PERCENT must be a number from 0 to 100.')
    if not math.isfinite(min_changed) or not 0 <= min_changed <= 100:
        raise CaptureError('SIMILARITY_MIN_CHANGED_PERCENT must be a number from 0 to 100.')

    # Resolve relative paths against the configuration directory, never cron's cwd.
    base = os.path.dirname(os.path.abspath(path))
    output_dir = values.get('CAPTURE_DIR', 'captures')
    if not output_dir:
        raise CaptureError('CAPTURE_DIR must not be empty.')
    output_dir = os.path.abspath(os.path.join(base, output_dir))
    executable = values.get('FFMPEG_PATH', '/usr/bin/ffmpeg')
    if not executable:
        raise CaptureError('FFMPEG_PATH must not be empty.')
    if os.path.dirname(executable):
        executable = os.path.abspath(os.path.join(base, executable))
    else:
        executable = shutil.which(executable)
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise CaptureError('FFmpeg executable was not found or is not executable.')
    return url, output_dir, executable, timeout, pixel_threshold, min_changed


def ffmpeg_failure_reason(diagnostics):
    """Map untrusted diagnostics to fixed messages; never return raw output."""
    text = diagnostics.decode('utf-8', errors='replace').lower()
    reasons = [
        (('401 unauthorized', '403 forbidden'),
         'Camera rejected authentication or access; check credentials and RTSP permissions.'),
        (('404 not found',),
         'Camera stream was not found; check the RTSP stream path.'),
        (('connection refused',),
         'Connection refused; check the camera address, RTSP port, and whether RTSP is enabled.'),
        (('no route to host', 'network is unreachable'),
         'Camera network is unreachable; check LAN connectivity.'),
        (('failed to resolve hostname', 'name or service not known', 'temporary failure in name resolution'),
         'Camera hostname could not be resolved; check the hostname or use its LAN IP address.'),
        (('connection timed out', 'operation timed out'),
         'Camera connection timed out; check the address, port, and network.'),
        (('461 unsupported transport',),
         'Camera rejected RTSP over TCP; check camera transport settings.'),
        (('invalid data found when processing input',),
         'Camera did not provide a decodable stream; check the RTSP path and camera video settings.'),
        (('unknown decoder', 'decoder not found', 'decoding requested, but no decoder found'),
         'This FFmpeg build lacks a decoder required by the camera stream.'),
        (('matches no streams', 'does not contain any stream'),
         'No video stream was found; check the RTSP stream path.'),
        (('unknown encoder',),
         'This FFmpeg build lacks the requested image encoder.'),
        (('permission denied', 'no space left on device', 'read-only file system'),
         'FFmpeg encountered a permission or storage error; check camera access and output storage.'),
    ]
    for patterns, reason in reasons:
        if any(pattern in text for pattern in patterns):
            return reason
    return 'Check camera configuration, connectivity, and video codec compatibility.'


def latest_image(output_dir):
    """Find the latest timestamped JPEG, excluding temporary files."""
    latest = None
    for entry in os.scandir(output_dir):
        if (re.match(r'^\d{8}T\d{6}_\d{6}Z\.jpg$', entry.name)
                and entry.is_file(follow_symlinks=False)
                and (latest is None or entry.name > latest)):
            latest = entry.name
    return os.path.join(output_dir, latest) if latest else None


def comparison_pixels(path, executable, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaptureError('Similarity comparison timed out.')
    command = [
        executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-threads', '1', '-i', path, '-map', '0:v:0', '-frames:v', '1',
        '-vf', 'scale={0}:{1}'.format(COMPARISON_WIDTH, COMPARISON_HEIGHT),
        '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1',
    ]
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=remaining,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CaptureError('Could not decode image for similarity comparison.')
    if result.returncode != 0 or len(result.stdout) != COMPARISON_WIDTH * COMPARISON_HEIGHT:
        raise CaptureError('Could not decode image for similarity comparison.')
    return result.stdout


def is_similar(temporary, output_dir, executable, deadline, pixel_threshold, min_changed):
    if min_changed == 0:
        return False
    try:
        previous = latest_image(output_dir)
        if previous is None:
            return False
        old = comparison_pixels(previous, executable, deadline)
        new = comparison_pixels(temporary, executable, deadline)
        changed = sum(abs(a - b) > pixel_threshold for a, b in zip(old, new))
        percent = 100.0 * changed / len(new)
        print('Image change: {0}/{1} comparison pixels ({2:.3f}%).'.format(
            changed, len(new), percent))
        return percent < min_changed
    except (CaptureError, OSError):
        # Preserve a successful capture if comparison is unavailable.
        print('Similarity comparison unavailable; saving image.', file=sys.stderr)
        return False


def capture(url, output_dir, executable, timeout, pixel_threshold, min_changed):
    os.makedirs(output_dir, exist_ok=True)
    # Keep the lock file in place: unlinking a flock file can allow overlapping runs.
    with open(os.path.join(output_dir, '.capture.lock'), 'a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                print('Capture skipped: another capture is running.')
                return
            raise

        started = datetime.datetime.utcnow()
        deadline = time.monotonic() + timeout
        destination = os.path.join(output_dir, started.strftime('%Y%m%dT%H%M%S_%fZ.jpg'))
        descriptor, temporary = tempfile.mkstemp(prefix='.capture-', suffix='.jpg', dir=output_dir)
        os.close(descriptor)
        try:
            command = [
                executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-y', '-rtsp_transport', 'tcp', '-i', url,
                '-map', '0:v:0', '-frames:v', '1', '-an', '-q:v', '2',
                '-f', 'image2', temporary,
            ]
            try:
                # FFmpeg diagnostics may echo credentials. Only fixed summaries
                # may be reported; never print raw output or exception text.
                result = subprocess.run(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise CaptureError('Capture timed out; check camera connectivity.')
            except OSError:
                raise CaptureError('Could not start FFmpeg.')
            if result.returncode != 0:
                raise CaptureError('FFmpeg capture failed (exit status {0}). {1}'.format(
                    result.returncode, ffmpeg_failure_reason(result.stderr)))
            with open(temporary, 'rb') as image:
                if image.read(2) != b'\xff\xd8' or os.path.getsize(temporary) < 4:
                    raise CaptureError('FFmpeg did not produce a JPEG image.')
                image.seek(-2, os.SEEK_END)
                if image.read(2) != b'\xff\xd9':
                    raise CaptureError('FFmpeg produced an incomplete JPEG image.')
            if is_similar(temporary, output_dir, executable, deadline,
                          pixel_threshold, min_changed):
                print('Capture skipped: image is essentially unchanged.')
                return
            os.replace(temporary, destination)
            print('Captured {0}'.format(started.strftime('%Y%m%dT%H%M%S_%fZ.jpg')))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default=os.path.join(SCRIPT_DIR, '.env'),
                        help='Configuration file (default: .env beside this script).')
    arguments = parser.parse_args()
    try:
        capture(*configuration(os.path.abspath(arguments.env)))
    except CaptureError as error:
        print('Capture error: {0}'.format(error), file=sys.stderr)
        return 1
    except OSError:
        # OS errors can contain configured paths. Do not print their raw text.
        print('Capture error: filesystem or system operation failed; check permissions and available disk space.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Capture interrupted.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
