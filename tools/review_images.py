"""Offline review of all managed captures and active references using HA's engine.

Never captures, connects to MQTT/HA, reads .env, trains, or runs retention.
Reviewed reference originals stay immutable; their review copies exclude identical
references from comparison. This is a calibration/review pass, not an accuracy test.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import math
import re
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camera_profile import camera_bounds
from data_layout import prediction_category
from ha_service.mqtt import occupant
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime
from ha_service.storage import Store, atomic_json, digest, safe_path, sync_directory


class CPUPacer:
    """Single-process average CPU budget, including all its worker threads.

    Pacing is cooperative, not an OS hard quota. Short operations can burst;
    sleeps repay CPU usage before the next image/file operation.
    """
    def __init__(self, percent):
        if not math.isfinite(percent) or not 1 <= percent <= 100:
            raise AppError('CPU percent must be from 1 to 100.')
        self.fraction = percent / 100
        self.wall = time.monotonic()
        self.cpu = time.process_time()
        self.last = self.wall

    def pause(self):
        target = max(self.wall + (time.process_time() - self.cpu) / self.fraction,
                     self.last + 0.02)
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def validate_plan(store, plan):
    if not isinstance(plan, list) or not 1 <= len(plan) <= 2500:
        raise AppError('Invalid review manifest image count.')
    for item in plan:
        source = item['source']
        destination = item['destination']
        if type(item['reference_original']) is not bool or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise AppError('Invalid review manifest entry.')
        safe_path(store.root, source)
        safe_path(store.root, destination)
        if item['reference_original']:
            prefix = 'references/versions/' + store.revision + '/'
            valid_source = source.startswith(prefix) and Store.valid_file('references', source[len(prefix):])
        else:
            valid_source = source.startswith('captures/') and Store.valid_file('captures', source[len('captures/'):])
        expected = 'captures/' + '/'.join(prediction_category(item['prediction']['label'])) + '/' + Path(source).name
        if not valid_source or destination != expected or not Store.valid_file('captures', destination[len('captures/'):]):
            raise AppError('Review manifest contains an unsupported image path.')


def review(root, apply=False, resume=None, cpu_percent=10):
    pacer = CPUPacer(cpu_percent)
    root = Path(root).absolute()
    if root.is_symlink() or (root / 'captures').is_symlink():
        raise AppError('Data/capture roots must not be symlinks.')
    options = validate({})
    left, right, rectangles = camera_bounds()
    options['camera_profile'] = {'horizontal_bounds': [left, right], 'excluded_rectangles': rectangles}
    # Locks are outside the image hierarchy and do not require any credentials.
    with ExitStack() as stack:
        for relative in ('.chicken-device.lock', 'captures/.capture.lock', 'captures/.sort.lock', 'service.lock'):
            path = root / relative
            if path.is_symlink():
                raise AppError('Review lock must not be a symlink.')
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise AppError('A local capture/service/import/sorter is running; stop it before review.') from None
        store = Store(root, options)
        runtime = Runtime(options, store)
        recognition = {key: options[key] for key in
                       ('chicken_area', 'texture_threshold', 'max_distance', 'min_margin', 'camera_profile')}
        if resume is not None:
            report = root / resume
            relative = report.relative_to(root)
            if len(relative.parts) != 2 or relative.parts[0] != 'reports' or not relative.name.startswith('review-'):
                raise AppError('Resume requires a reports/review-* directory in this data root.')
            manifest_path = safe_path(root, relative.as_posix() + '/manifest.json')
            if manifest_path.stat().st_size > 4 * 1024 * 1024:
                raise AppError('Review manifest exceeds the size limit.')
            manifest = json.loads(manifest_path.read_text())
            if manifest['reference_revision'] != store.revision or manifest['recognition'] != recognition:
                raise AppError('Reference version or recognition settings changed; do not resume stale predictions.')
            plan = manifest['images']
        else:
            references = runtime._references()
            reference_files = store.files('references')
            reference_hashes = {store.reference_root() / item['path']: item['sha256'] for item in reference_files}
            sources = [(store.root / 'captures' / item['path'], item['sha256'], False)
                       for item in store.files('captures')]
            sources += [(path, sha, True) for path, sha in reference_hashes.items()]
            plan = []
            targets = {}
            for source, sha, is_reference in sources:
                matches = [item for item in references if reference_hashes[item[1]] == sha]
                comparison = [item for item in references if reference_hashes[item[1]] != sha] if matches else references
                testable = (len({item[0] for item in comparison}) >= 2
                            and all(any(other[0] == match[0] for other in comparison) for match in matches))
                prediction = (runtime.predict(source, comparison) if testable
                              else {'label': 'unknown', 'distance': None, 'margin': None})
                relative = '/'.join(prediction_category(prediction['label'])) + '/' + source.name
                destination = safe_path(root / 'captures', relative)
                if relative in targets and targets[relative] != sha:
                    raise AppError('Different source images share a destination; originals preserved. Resolve filenames before review.')
                targets[relative] = sha
                if destination.exists() and digest(destination.read_bytes()) != sha:
                    raise AppError('A categorized destination contains different data; originals preserved.')
                if is_reference and destination.exists() and os.path.samefile(str(source), str(destination)):
                    raise AppError('A review destination is linked to a golden reference; detach it before review.')
                plan.append({'source': source.relative_to(root).as_posix(),
                             'destination': 'captures/' + relative, 'sha256': sha,
                             'reference_original': is_reference, 'reference_check': bool(matches),
                             'self_check_testable': testable, 'prediction': prediction})
                pacer.pause()
        validate_plan(store, plan)
        # Resume must tolerate an original already moved, but must never trust a
        # missing/corrupt source AND destination or touch a path outside images.
        for item in plan:
            source = safe_path(root, item['source'])
            destination = safe_path(root, item['destination'])
            if source.exists():
                if digest(source.read_bytes()) != item['sha256']:
                    raise AppError('A source changed since prediction; originals preserved.')
            elif item['reference_original'] or not destination.exists():
                raise AppError('A planned original is missing and no valid moved copy is available.')
            if destination.exists() and digest(destination.read_bytes()) != item['sha256']:
                raise AppError('A destination changed since prediction; nothing will be overwritten.')
            if item['reference_original'] and destination.exists() and os.path.samefile(str(source), str(destination)):
                raise AppError('A review copy is linked to a golden reference; detach it before review.')
            pacer.pause()
        counts = Counter(item['prediction']['label'] for item in plan)
        if not apply:
            reference_count = sum(item['reference_original'] for item in plan)
            print('Review preview: {} captures + {} active references; no images moved.'.format(
                len(plan) - reference_count, reference_count))
            return {'images': len(plan), 'counts': dict(counts)}
        # All predictions and destination conflicts are checked before relocation.
        if resume is None:
            report = root / 'reports' / ('review-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
            safe_path(root, report.relative_to(root).as_posix())
            report.mkdir(parents=True)
            atomic_json(report / 'manifest.json', {'reference_revision': store.revision,
                                                  'recognition': recognition, 'images': plan})
        store.require_space(sum(safe_path(root, item['source']).stat().st_size for item in plan
                                if item['reference_original']) + 16 * 1024 * 1024)
        print('{} {} inputs; one worker, CPU pacing target {}% of one core.'.format(
            'Resuming' if resume else 'Applying', len(plan), cpu_percent), flush=True)
        for item in plan:
            source = safe_path(root, item['source'])
            destination = safe_path(root, item['destination'])
            if source.exists() and digest(source.read_bytes()) != item['sha256']:
                raise AppError('A source image changed during review; remaining originals preserved.')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source != destination:
                if not destination.exists():
                    if item['reference_original']:
                        # Never hardlink review copies to immutable golden refs:
                        # editing a preview must not mutate the reference snapshot.
                        descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=str(destination.parent))
                        try:
                            with os.fdopen(descriptor, 'wb') as handle, source.open('rb') as incoming:
                                shutil.copyfileobj(incoming, handle)
                                handle.flush()
                                os.fsync(handle.fileno())
                            os.link(temporary, str(destination))
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
                    else:
                        os.link(str(source), str(destination))
                if digest(destination.read_bytes()) != item['sha256']:
                    raise AppError('Destination verification failed; source preserved.')
                sync_directory(destination.parent)
                if not item['reference_original'] and source.exists():
                    source.unlink()
            stamp = source.stem
            result = {'timestamp': stamp, 'occupant': occupant(item['prediction']),
                      'distance': item['prediction']['distance'], 'margin': item['prediction']['margin'],
                      'reference_check': item['reference_check'], 'self_check_testable': item['self_check_testable']}
            result_path = store.root / 'results' / (stamp + '.json')
            if result_path.exists():
                if result_path.is_symlink():
                    raise AppError('Result file must not be a symlink.')
                try:
                    unchanged = json.loads(result_path.read_text()) == result
                except (ValueError, UnicodeError):
                    unchanged = False
                if unchanged:
                    pacer.pause()
                    continue
                backup = report / 'results-before'
                backup.mkdir(exist_ok=True)
                saved = backup / result_path.name
                if not saved.exists():
                    shutil.copyfile(str(result_path), str(saved))
            atomic_json(result_path, result)
            pacer.pause()
        # Check every input survives at its review destination, and refs survive
        # unchanged at their original paths. Do not invoke HA retention here.
        for item in plan:
            if digest(safe_path(root, item['destination']).read_bytes()) != item['sha256']:
                raise AppError('Review checksum verification failed; consult the private manifest.')
            if item['reference_original'] and digest(safe_path(root, item['source']).read_bytes()) != item['sha256']:
                raise AppError('Reference original changed; consult the private manifest.')
            pacer.pause()
        summary = {'images': len(plan), 'counts': dict(counts), 'complete': True,
                   'reference_checks': sum(item['reference_check'] for item in plan),
                   'note': 'Self-excluded reference checks and correlated captures are not an independent accuracy benchmark.'}
        atomic_json(report / 'summary.json', summary)
        print('Reviewed {} inputs: {} empty, {} unknown, {} named predictions.'.format(
            len(plan), counts.get('no_chicken', 0), counts.get('unknown', 0),
            sum(count for label, count in counts.items() if label not in ('no_chicken', 'unknown'))))
        print('All input checksums verified; reviewed reference originals preserved. No retention was run.')
        print('Private review manifest and summary: ' + report.relative_to(root).as_posix())
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--apply', action='store_true', help='Categorize captures and save reference review copies; default is a prediction preview')
    parser.add_argument('--resume', type=Path, help='Resume an existing reports/review-* manifest without recomputing predictions')
    parser.add_argument('--cpu-percent', type=float, default=10, help='Cooperative average CPU budget as percent of one core (default: 10)')
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        import cv2
        cv2.setNumThreads(1)
        review(arguments.root, arguments.apply, arguments.resume, arguments.cpu_percent)
        return 0
    except AppError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Offline review stopped; originals/durable copies are preserved. Check images, references and permissions.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
