#!/usr/bin/env python3
"""Opt-in integration: real Codex, a local standard-only proxy, no real credentials."""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'codex-model-picker-policy'


@unittest.skipUnless(os.environ.get('CODEX_POLICY_REAL_CODEX') == '1', 'set CODEX_POLICY_REAL_CODEX=1 to use the installed Codex')
class RealCodexTests(unittest.TestCase):
    def test_extra_models_load_in_real_codex(self):
        self.assertTrue(shutil.which('codex'), 'codex must be on PATH')
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                # A standard proxy ignores CLIProxyAPI's optional query parameter.
                self.wfile.write(json.dumps({'data': [
                    {'id': 'extra-coding-model'},
                    {'id': 'extra-vision-model', 'input_modalities': ['text', 'image'], 'context_window': 32768},
                    {'id': 'gpt-image-example'},
                    {'id': 'codex-auto-review'},
                ]}).encode())

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                config = home / 'config.toml'
                config.write_text('model_provider = "fixture"\n[model_providers.fixture]\n'
                                  'name = "Local test"\nwire_api = "responses"\n'
                                  f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n')
                env = {**os.environ, 'CODEX_HOME': directory}
                for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_CA_CERTIFICATE', 'SSL_CERT_FILE'):
                    env.pop(key, None)
                result = subprocess.run([str(SCRIPT), 'install'], cwd=directory, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run(['codex', 'debug', 'models'], cwd=directory, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, 'real Codex failed to load the generated catalog')
                models = {m['slug']: m for m in json.loads(result.stdout)['models']}
                self.assertEqual(set(models), {'extra-coding-model', 'extra-vision-model', 'gpt-image-example', 'codex-auto-review'})
                self.assertEqual(models['extra-coding-model']['visibility'], 'list')
                self.assertFalse(models['extra-coding-model'].get('context_window'))
                self.assertEqual(models['extra-coding-model']['supported_reasoning_levels'], [])
                self.assertEqual(models['extra-coding-model']['input_modalities'], ['text'])
                self.assertEqual(models['extra-vision-model']['context_window'], 32768)
                self.assertEqual(models['extra-vision-model']['visibility'], 'list')
                self.assertEqual(models['gpt-image-example']['visibility'], 'hide')
                self.assertEqual(models['codex-auto-review']['visibility'], 'hide')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
