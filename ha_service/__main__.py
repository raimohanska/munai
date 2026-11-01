"""Supervisor starts this module; settings are /data/options.json, never .env."""
import fcntl
import logging
import os
from pathlib import Path
import signal
import threading

from .api import TransferServer, tls_context
from .mqtt import MQTT
from .options import AppError, read_options
from .runtime import Runtime
from .storage import Store

LOG = logging.getLogger('munai')


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S %z')
    server = None
    publisher = None
    stop = threading.Event()
    lock = None
    try:
        options = read_options()
        LOG.setLevel(logging.DEBUG if options['log_level'] == 'debug' else logging.INFO)
        import cv2
        cv2.setNumThreads(1)
        root = Path('/data')
        if root.is_symlink() or (root / 'service.lock').is_symlink():
            raise AppError('Data directory and service lock must not be symlinks.')
        root.mkdir(exist_ok=True, mode=0o700)
        lock = (root / 'service.lock').open('a')
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AppError('Another Munai service instance is already running.') from None
        store = Store(root, options)
        if options['mqtt_enabled']:
            publisher = MQTT(options)
        runtime = Runtime(options, store, publisher)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda signum, frame: stop.set())
        if options['transfer_enabled']:
            context, fingerprint = tls_context(store.root)
            server = TransferServer(('0.0.0.0', 8443), context, store, options['transfer_tokens'],
                                    runtime.validate_reference, runtime.status)
            threading.Thread(target=server.serve_forever, name='transfers', daemon=True).start()
            LOG.info('HTTPS transfers ready on port 8443 (host port must be enabled in HA Network settings).')
            LOG.info('Transfer certificate SHA256 fingerprint: %s', fingerprint)
            if not options['transfer_tokens']:
                LOG.info('Transfers locked: pair the devcontainer CLI and add its token digest in HA Configuration.')
        if publisher:
            publisher.start()
        LOG.info('Munai started. Capture %s; MQTT %s; persistent captures %s; interval %d seconds.',
                 'enabled' if options['capture_enabled'] else 'disabled',
                 'enabled' if options['mqtt_enabled'] else 'disabled',
                 'enabled' if options['store_captures'] else 'disabled', options['capture_interval'])
        runtime.run(stop)
        return 0
    except AppError as error:
        LOG.error('%s', error)
        return 1
    except Exception:
        LOG.error('App startup failed; check dependencies, Configuration, HTTPS files and storage permissions.')
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if publisher:
            try:
                publisher.close()
            except Exception:
                pass
        if lock:
            lock.close()
        LOG.info('Munai stopped.')


if __name__ == '__main__':
    raise SystemExit(main())
