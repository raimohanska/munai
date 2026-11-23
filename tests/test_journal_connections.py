"""Exercise concurrent Ingress requests over loopback using temporary storage."""
from http.client import HTTPConnection
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from ha_service.journal_web import JournalServer
from ha_service.options import validate
from ha_service.runtime import Runtime
from ha_service.storage import Store


class ConnectionTests(unittest.TestCase):
    def test_status_loads_while_first_connection_is_idle(self):
        class LoopbackIngress(JournalServer):
            def get_request(self):
                sock, peer = super().get_request()
                return sock, ('172.30.32.2', peer[1])

        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as root:
            options = validate({})
            runtime = Runtime(options, Store(Path(root), options))
            with LoopbackIngress(('127.0.0.1', 0), runtime.journal, runtime) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    with socket.create_connection(server.server_address):
                        client = HTTPConnection(*server.server_address, timeout=2)
                        try:
                            client.request('GET', '/status')
                            response = client.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertIn(b'<h1>Status</h1>', response.read())
                        finally:
                            client.close()
                finally:
                    server.shutdown()
                    thread.join()
