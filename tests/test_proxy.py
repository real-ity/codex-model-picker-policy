#!/usr/bin/env python3
"""End-to-end tests with a local HTTP proxy and an isolated Codex home."""
import copy
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'codex-model-picker-policy'


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                state = self.server.state
                state['requests'].append((self.path, dict(self.headers)))
                rich = 'client_version' in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                status = state.get('rich_status' if rich else 'status', 200)
                payload = state['catalog'] if rich else state['listing']
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                if status == 302:
                    self.send_header('Location', '/redirected')
                self.end_headers()
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'codex home'
        self.home.mkdir()
        self.config = self.home / 'config.toml'
        self.catalog = self.home / 'model-catalog.json'
        self.env = {**os.environ, 'CODEX_HOME': str(self.home),
                    'PATH': f'{ROOT / "tests/bin"}:{os.environ["PATH"]}',
                    'CODEX_POLICY_TEST_ROOT': str(ROOT)}
        self.env.pop('CODEX_POLICY_REQUIRE_UNPINNED', None)
        self.models = [
            {'slug': 'coding-alias', 'display_name': 'Coding alias', 'visibility': 'hide',
             'context_window': 123456, 'input_modalities': ['text', 'image'],
             'supported_reasoning_levels': [{'effort': 'high', 'description': 'Think harder'}]},
            {'slug': 'gpt-image-2.5', 'visibility': 'list'},
            {'slug': 'vendor/FLUX-1', 'visibility': 'list'},
            {'slug': 'custom-renderer', 'visibility': 'list', 'output_modalities': ['image']},
            {'slug': 'codex-auto-review', 'visibility': 'list'},
            {'slug': 'vendor/GLM-5', 'visibility': 'list'},
            {'slug': 'deepseek-v4', 'visibility': 'list'},
        ]
        self.server.state = {'catalog': {'models': copy.deepcopy(self.models), 'revision': 'fixture'},
                             'listing': {'data': [{'id': m['slug']} for m in reversed(self.models)]},
                             'requests': []}
        self.config.write_text(
            'model = "coding-alias"\nmodel_provider = "proxy"\n\n'
            '[model_providers.inactive]\nbase_url = "https://unused.invalid/v1"\n\n'
            f'[model_providers.proxy]\nbase_url = "{self.url}/v1"\n'
            'experimental_bearer_token = "test-secret-only"\nwire_api = "responses"\n')

    def invoke(self, *args, ok=True):
        result = subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True, timeout=10)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('test-secret-only', result.stdout + result.stderr)
        return result

    def test_generate_preserves_metadata_and_config(self):
        before = self.config.read_bytes()
        self.invoke('generate')
        catalog = json.loads(self.catalog.read_text())
        expected = copy.deepcopy(self.models)
        for model in expected:
            model['visibility'] = 'list' if model['slug'] in ('coding-alias', 'vendor/GLM-5', 'deepseek-v4') else 'hide'
            model['supported_in_api'] = True
        self.assertEqual({m['slug']: m for m in catalog['models']}, {m['slug']: m for m in expected})
        self.assertEqual(catalog['revision'], 'fixture')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.catalog.stat().st_mode & 0o777, 0o600)
        requests = self.server.state['requests']
        self.assertEqual([p for p, _ in requests], ['/v1/models', '/v1/models?client_version=test'])
        self.assertTrue(all(h.get('Authorization') == 'Bearer test-secret-only' for _, h in requests))

    def test_custom_output_and_root_url(self):
        output = Path(self.temp.name) / 'nested' / 'catalog.json'
        self.invoke('generate', '--base-url', self.url, '--output', str(output))
        self.assertTrue(output.exists())
        self.assertFalse(self.catalog.exists())
        self.assertEqual(self.server.state['requests'][0][0], '/v1/models')

    def test_endpoint_url_does_not_duplicate_models(self):
        self.invoke('generate', '--base-url', self.url + '/v1/models/')
        self.assertEqual(self.server.state['requests'][0][0], '/v1/models')

    def test_install_dry_run_leaves_everything_untouched(self):
        before = self.config.read_bytes()
        self.invoke('install', '--dry-run')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.catalog.exists())
        self.assertEqual(list(self.home.iterdir()), [self.config])

    def test_live_list_json_has_no_progress_noise_or_writes(self):
        result = self.invoke('list', '--live', '--json')
        self.assertEqual(len(json.loads(result.stdout)['models']), len(self.models))
        self.assertFalse(self.catalog.exists())

    def test_invalid_responses_preserve_existing_files(self):
        original = copy.deepcopy(self.server.state)
        cases = [
            {'listing': {'data': []}},
            {'listing': {'data': [{'id': ''}]}},
            {'listing': {'data': [{'id': 'x'}, {'id': 'x'}]}},
            {'listing': b'<html>not JSON</html>'},
            {'catalog': {'models': []}},
            {'catalog': {'models': [{'slug': 'coding-alias'}, {'slug': 'coding-alias'}]}},
            {'status': 401, 'listing': b'test-secret-only'},
            {'rich_status': 503, 'catalog': b'test-secret-only'},
            {'rich_status': 401, 'catalog': b'test-secret-only'},
            {'catalog': b'not JSON'},
            {'catalog': {'unexpected': []}},
            {'status': 302},
        ]
        before = self.config.read_bytes()
        previous = '{"models": [{"slug": "previous", "visibility": "list"}]}'
        self.catalog.write_text(previous)
        for case in cases:
            with self.subTest(case=list(case)):
                self.server.state = {**copy.deepcopy(original), **case}
                self.invoke('install', ok=False)
                self.assertEqual(self.catalog.read_text(), previous)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(len(list(self.home.iterdir())), 2)
                self.assertFalse(any(path == '/redirected' for path, _ in self.server.state['requests']))

    def test_environment_auth_and_headers(self):
        self.env['TEST_PROXY_KEY'] = 'env-test-key'
        self.env['TEST_ROUTING'] = 'fixture-route'
        self.config.write_text(self.config.read_text() + '\nenv_key = "TEST_PROXY_KEY"\n'
                               '[model_providers.proxy.http_headers]\nX-Static = "fixture"\n'
                               '[model_providers.proxy.env_http_headers]\nX-Route = "TEST_ROUTING"\n')
        self.invoke('generate')
        for _, headers in self.server.state['requests']:
            self.assertEqual(headers['Authorization'], 'Bearer env-test-key')
            self.assertEqual(headers['X-Static'], 'fixture')
            self.assertEqual(headers['X-Route'], 'fixture-route')

    def test_missing_key_fails_before_network(self):
        self.env.pop('UNSET_PROXY_TEST_KEY', None)
        result = self.invoke('generate', '--api-key-env', 'UNSET_PROXY_TEST_KEY', ok=False)
        self.assertIn('environment variable is unset', result.stderr)
        self.assertEqual(self.server.state['requests'], [])

    def test_cli_rejection_keeps_previous_catalog(self):
        previous = '{"models": [{"slug": "previous", "visibility": "list"}]}'
        self.catalog.write_text(previous)
        self.env['CODEX_POLICY_REJECT_CATALOG'] = '1'
        before = self.config.read_bytes()
        self.invoke('install', ok=False)
        self.assertEqual(self.catalog.read_text(), previous)
        self.assertEqual(self.config.read_bytes(), before)

    def test_file_profile_merges_provider_and_preserves_config(self):
        profile = self.home / 'work.config.toml'
        self.config.write_text(self.config.read_text().replace('model_provider = "proxy"', 'model_provider = "inactive"'))
        profile.write_text('model_provider = "proxy"\n[model_providers.proxy.http_headers]\nX-Profile = "work"\n')
        before = self.config.read_bytes(), profile.read_bytes()
        self.invoke('generate', '--profile', 'work')
        self.assertTrue((self.home / 'model-catalog-work.json').exists())
        self.assertFalse(self.catalog.exists())
        self.assertEqual(before, (self.config.read_bytes(), profile.read_bytes()))
        for _, headers in self.server.state['requests']:
            self.assertEqual(headers['X-Profile'], 'work')
            self.assertEqual(headers['Authorization'], 'Bearer test-secret-only')

    def test_profile_install_updates_only_profile(self):
        profile = self.home / 'work.config.toml'
        profile.write_text('model = "coding-alias"\nmodel_catalog_json = "/old/catalog.json"\n')
        before = self.config.read_bytes()
        self.invoke('install', '--profile', 'work')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn(str(self.home / 'model-catalog-work.json'), profile.read_text())
        self.invoke('check', '--profile', 'work')

    def test_invalid_profiles_fail_before_network(self):
        for name in ('missing', '../escape', 'work/name'):
            with self.subTest(name=name):
                self.invoke('generate', '--profile', name, ok=False)
        self.assertEqual(self.server.state['requests'], [])
        self.assertFalse(self.catalog.exists())

    def test_legacy_inline_profiles_need_migration(self):
        self.config.write_text('profile = "work"\n' + self.config.read_text() +
                               '\n[profiles.work]\nmodel_provider = "proxy"\n')
        self.invoke('generate', ok=False)
        self.assertEqual(self.server.state['requests'], [])

    def test_config_overrides_select_provider_and_nested_values(self):
        self.config.write_text(self.config.read_text().replace('model_provider = "proxy"', 'model_provider = "inactive"'))
        self.invoke('generate', '-c', 'model_provider=proxy', '--config',
                    'model_providers.proxy.http_headers={"X-Override"="selected"}')
        for _, headers in self.server.state['requests']:
            self.assertEqual(headers['X-Override'], 'selected')
        self.assertIn('model_provider = "inactive"', self.config.read_text())

    def test_profile_and_credentials_are_protected_output_paths(self):
        profile = self.home / 'work.config.toml'
        profile.write_text('model = "coding-alias"\n')
        for output in (profile, self.home / 'auth.json', self.config):
            with self.subTest(output=output.name):
                self.invoke('generate', '--profile', 'work', '--output', str(output), ok=False)
        self.assertEqual(self.server.state['requests'], [])

    def test_check_detects_live_drift(self):
        self.invoke('install')
        self.invoke('check', '--live')
        self.server.state['listing']['data'].append({'id': 'new-model'})
        self.server.state['catalog']['models'].append({'slug': 'new-model'})
        result = self.invoke('check', '--live', ok=False)
        self.assertIn('new-model', result.stderr)
        self.assertIn('run generate', result.stderr)

    def test_check_rejects_uninstalled_catalog(self):
        self.invoke('generate')
        result = self.invoke('check', ok=False)
        self.assertIn('does not point to the managed catalog', result.stderr)

    def test_invalid_url_timeout_and_output_fail_without_writes(self):
        for flags in [('--base-url', 'ftp://example.com'), ('--base-url', self.url + '?key=secret'),
                      ('--base-url', 'https://user:secret@example.com'), ('--timeout', 'nan'),
                      ('--timeout', '0'), ('--output', str(self.config)),
                      ('--source', 'codex', '--base-url', self.url)]:
            with self.subTest(flags=flags):
                self.invoke('generate', *flags, ok=False)
                self.assertFalse(self.catalog.exists())
        self.assertEqual(self.server.state['requests'], [])

    def test_standard_only_proxy_accepts_new_models_without_invented_capabilities(self):
        self.server.state['listing'] = {'data': [{'id': 'new-coding-model'}, {'id': 'gpt-image-9'},
                                               {'id': 'deepseek-v4'}, {'id': 'gpt-5.6-sol'}]}
        self.server.state['catalog'] = copy.deepcopy(self.server.state['listing'])
        self.invoke('generate')
        result = json.loads(self.catalog.read_text())
        models = {m['slug']: m for m in result['models']}
        self.assertEqual(set(models), {'new-coding-model', 'gpt-image-9', 'deepseek-v4', 'gpt-5.6-sol'})
        self.assertEqual(models['new-coding-model']['visibility'], 'list')
        self.assertEqual(models['deepseek-v4']['visibility'], 'list')
        self.assertEqual(models['gpt-image-9']['visibility'], 'hide')
        generic = models['new-coding-model']
        self.assertFalse(generic.get('context_window'))
        self.assertFalse(generic.get('supported_reasoning_levels'))
        self.assertEqual(generic.get('input_modalities'), ['text'])
        self.assertEqual(models['gpt-5.6-sol']['description'], 'normal')

    def test_unsupported_rich_endpoint_falls_back(self):
        for status in (400, 404, 405, 422, 501):
            with self.subTest(status=status):
                self.server.state['rich_status'] = status
                self.server.state['catalog'] = b'extension unsupported'
                self.invoke('generate')
                models = json.loads(self.catalog.read_text())['models']
                self.assertEqual({m['slug'] for m in models}, {m['slug'] for m in self.models})

    def test_inventory_is_authoritative_over_partial_rich_metadata(self):
        rich = copy.deepcopy(self.models[0])
        self.server.state['catalog']['models'] = [rich, {'slug': 'stale-not-in-inventory'}]
        self.invoke('generate')
        models = {m['slug']: m for m in json.loads(self.catalog.read_text())['models']}
        self.assertEqual(set(models), {m['slug'] for m in self.models})
        self.assertEqual(models['coding-alias']['context_window'], 123456)
        self.assertEqual(models['vendor/GLM-5']['visibility'], 'list')

    def test_direct_rich_catalog_is_supported(self):
        self.server.state['listing'] = copy.deepcopy(self.server.state['catalog'])
        self.invoke('generate')
        models = {m['slug']: m for m in json.loads(self.catalog.read_text())['models']}
        self.assertEqual(set(models), {m['slug'] for m in self.models})
        self.assertEqual(models['coding-alias']['context_window'], 123456)

    def test_exclusions_are_explicit_case_insensitive_and_saved_for_check(self):
        self.invoke('install', '--exclude', '*GLM*', '--exclude', 'DEEPSEEK-*')
        result = json.loads(self.catalog.read_text())
        models = {m['slug']: m for m in result['models']}
        self.assertEqual(models['vendor/GLM-5']['visibility'], 'hide')
        self.assertEqual(models['deepseek-v4']['visibility'], 'hide')
        self.assertEqual(models['coding-alias']['visibility'], 'list')
        self.assertEqual(result['_model_picker']['exclude'], ['*GLM*', 'DEEPSEEK-*'])
        self.invoke('check')
        self.invoke('refresh')
        models = {m['slug']: m for m in json.loads(self.catalog.read_text())['models']}
        self.assertEqual(models['vendor/GLM-5']['visibility'], 'hide')
        self.invoke('refresh', '--clear-exclusions')
        models = {m['slug']: m for m in json.loads(self.catalog.read_text())['models']}
        self.assertEqual(models['vendor/GLM-5']['visibility'], 'list')
        self.assertEqual(models['gpt-image-2.5']['visibility'], 'hide')

    def test_non_chat_entries_are_hidden_but_multimodal_coding_is_visible(self):
        ids = ['text-embedding-3-large', 'whisper-1', 'tts-1', 'omni-moderation-latest',
               'gpt-realtime', 'vendor/rerank-v3', 'sora-2', 'vision-coder']
        entries = [{'id': slug} for slug in ids]
        entries[-1]['input_modalities'] = ['text', 'image', 'audio']
        entries.append({'id': 'speech-alias', 'output_modalities': ['audio']})
        entries.append({'id': 'vector-alias', 'model_specialty': 'embedding'})
        self.server.state['listing'] = self.server.state['catalog'] = {'data': entries}
        self.invoke('generate')
        models = json.loads(self.catalog.read_text())['models']
        self.assertEqual([m['slug'] for m in models if m['visibility'] == 'list'], ['vision-coder'])

    def test_rich_catalog_keeps_inventory_image_evidence(self):
        self.server.state['listing'] = {'data': [{'id': 'render-alias', 'output_modalities': ['image']}]}
        self.server.state['catalog'] = {'models': [{'slug': 'render-alias'}]}
        self.invoke('generate')
        self.assertEqual(json.loads(self.catalog.read_text())['models'][0]['visibility'], 'hide')

    def test_conflicting_catalog_override_cannot_report_install_success(self):
        before = self.config.read_bytes()
        self.invoke('install', '-c', 'model_catalog_json="/other/catalog.json"', ok=False)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.catalog.exists())
        self.assertEqual(self.server.state['requests'], [])

    def test_built_in_openai_url_override_is_discovered(self):
        self.config.write_text(f'openai_base_url = "{self.url}/v1"\n')
        self.env['OPENAI_API_KEY'] = 'fixture-openai-key'
        self.invoke('generate')
        self.assertEqual(len(self.server.state['requests']), 2)
        self.assertTrue(all(h['Authorization'] == 'Bearer fixture-openai-key' for _, h in self.server.state['requests']))

    def test_no_arguments_generates_and_inventory_order_is_stable(self):
        self.invoke()
        first = self.catalog.read_bytes()
        self.server.state['listing']['data'].reverse()
        self.invoke('generate')
        self.assertEqual(self.catalog.read_bytes(), first)

    def test_partial_inventory_cannot_replace_complete_catalog(self):
        self.invoke('generate')
        before = self.catalog.read_bytes()
        self.server.state['listing']['has_more'] = True
        self.invoke('generate', ok=False)
        self.assertEqual(self.catalog.read_bytes(), before)

    def test_missing_optional_environment_header_is_omitted(self):
        self.env.pop('UNSET_OPTIONAL_ROUTING_HEADER', None)
        self.config.write_text(self.config.read_text() +
                               '\n[model_providers.proxy.env_http_headers]\nX-Optional = "UNSET_OPTIONAL_ROUTING_HEADER"\n')
        self.invoke('generate')
        self.assertTrue(all('X-Optional' not in h for _, h in self.server.state['requests']))

    def test_provider_query_parameters_are_preserved(self):
        self.config.write_text(self.config.read_text() +
                               '\n[model_providers.proxy.query_params]\napi-version = "2026-01"\nroute = "a b"\n')
        self.invoke('generate')
        requests = self.server.state['requests']
        self.assertEqual(len(requests), 2)
        for path, _ in requests:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            self.assertEqual(query['api-version'], ['2026-01'])
            self.assertEqual(query['route'], ['a b'])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(requests[1][0]).query)['client_version'], ['test'])

    def test_config_multiline_value_is_not_corrupted(self):
        original = self.config.read_text()
        self.config.write_text('note = """\nmodel_catalog_json = "not a setting"\n"""\n' + original)
        before = self.config.read_bytes()
        self.invoke('install', ok=False)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.catalog.exists())


if __name__ == '__main__':
    unittest.main()
