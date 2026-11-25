"""Shared local server lifecycle for API and browser regression tests."""

import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from crossplay_server import Handler
from tools.mock_nyt import MockHandler


class QuietMockHandler(MockHandler):
    def log_message(self, format, *args):
        pass


@contextmanager
def mock_server():
    with ThreadingHTTPServer(('127.0.0.1', 0), QuietMockHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            thread.join()
            with Handler.sessions_lock:
                Handler.sessions.clear()


def trace_quackle_workers(page):
    """Observe real worker requests and let tests hold one request in flight."""
    page.evaluate("""() => {
      const BrowserWorker = window.Worker;
      const trace = window.workerTrace = {count: 0, requests: [], terminations: 0};
      window.Worker = class extends BrowserWorker {
        constructor(...args) {
          super(...args);
          trace.count++;
        }
        postMessage(message, ...options) {
          trace.requests.push(message);
          if (trace.hold?.(message)) {
            trace.hold = null;
            trace.release = () => {
              trace.release = null;
              super.postMessage(message, ...options);
            };
          } else {
            super.postMessage(message, ...options);
          }
        }
        terminate() {
          trace.terminations++;
          super.terminate();
        }
      };
    }""")
