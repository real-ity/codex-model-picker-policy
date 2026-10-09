# Codex model catalog generator

Make the models advertised by your custom proxy available in Codex's model picker,
including models that Codex does not already know about.

```sh
./codex-model-picker-policy
```

The script reads Codex's selected provider and credentials from
`$CODEX_HOME/config.toml` (default: `~/.codex/config.toml`), queries that endpoint,
and writes `$CODEX_HOME/model-catalog.json`. It does not change your configuration.
No proxy-specific URL or token needs to be entered again.

Requires **Python 3.11+** and a **Codex CLI supporting `debug models --bundled`**.
No Python packages are required. Works on macOS, Linux, and WSL.

## Use the generated catalog

To generate the file and configure Codex to use it in one step:

```sh
./codex-model-picker-policy install
./codex-model-picker-policy check --live
```

`install` adds the root `model_catalog_json` setting to the selected configuration
file, preserves unrelated settings, and backs up changed configuration. Repeating
it does not create another backup if the setting is unchanged. Current Codex TUIs
read the picker from a shared app-server daemon whose model list is cached in
memory, so `install` restarts a running daemon for you; if none is running, restart
Codex to reload the picker. Pass `--no-daemon-restart` to leave the daemon alone
and receive the restart reminder instead.

To manage that setting yourself, use the generated file's absolute path:

```toml
model_catalog_json = "/absolute/path/to/model-catalog.json"
```

The catalog is a snapshot. Rerun the script after adding or removing models from
your proxy. `generate` and `refresh` are explicit names for the default command.

```sh
./codex-model-picker-policy generate --dry-run
./codex-model-picker-policy generate --output ./model-catalog.json
./codex-model-picker-policy list
./codex-model-picker-policy list --live --json
./codex-model-picker-policy remove
```

`--dry-run` discovers and validates without changing files. `list` shows IDs and
visibility. `remove` removes the marked managed setting, backs up and clears
Codex's `models_cache.json`, and retains the generated catalog file. A running
app-server daemon is restarted after removal. Unmanaged catalog settings and
settings inherited from the user
configuration are retained and reported. The selected provider can advertise the
same models again, so removing the policy does not restore the bundled model list.
`check` verifies the configuration pointer and that Codex accepts the catalog;
`check --live` also detects changed IDs and picker visibility at the endpoint.
On macOS, checks include Codex executables in installed Codex/ChatGPT app bundles.

## Restore factory models

To reset the picker to the models shipped with your installed Codex CLI:

```sh
./codex-model-picker-policy reset --dry-run
./codex-model-picker-policy reset
./codex-model-picker-policy check --live
```

`reset` restores bundled model IDs, metadata, and visibility without contacting
your provider, clears personal exclusions and the model cache, and backs up
changed configuration and catalog files. It preserves your provider, credentials,
and selected model. A running app-server daemon is restarted to load the reset
picker.

The bundled catalog is installed as `model_catalog_json` so a proxy's model
inventory cannot replace it on restart. `check --live` compares a factory catalog
with the installed CLI's bundle instead of the proxy. Rerun `reset` after a Codex
upgrade to pick up its new bundled models. Use `install` to return to your proxy's
inventory, or `remove` to return to ordinary Codex discovery. A launch-time
`-c model_catalog_json=...` override must be removed if it selects another catalog.

`reset --profile NAME` changes only that profile's catalog and configuration.
The model cache is shared across profiles; clearing it causes other profiles to
reload their inventory too. Backups use the original filename with `.bak.*`.

## Follow the same Codex configuration

If you launch Codex with a profile or configuration overrides, supply the same
ones here:

```sh
# Codex: codex --profile work
./codex-model-picker-policy --profile work
./codex-model-picker-policy install --profile work

# Codex: codex -c 'model_provider="myproxy"'
./codex-model-picker-policy -c 'model_provider="myproxy"'
```

Selection follows user `config.toml`, then `$CODEX_HOME/<name>.config.toml` for
`--profile`, then `-c`/`--config` overrides. Profile generation writes
`model-catalog-<name>.json`; profile installation edits only that profile file.
Legacy inline profiles are rejected with migration guidance, matching current
[Codex profile configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles).

The script reads user configuration; it does not attach to an existing session
or load managed/system configuration. Project-local provider settings are not
used. A session launched with different flags or environment variables can have a
different connection. Built-in OpenAI endpoint overrides use `openai_base_url` and
`OPENAI_API_KEY`.

Optional discovery overrides are also available:

```sh
./codex-model-picker-policy --provider another-proxy
./codex-model-picker-policy --base-url http://localhost:8317/v1 --api-key-env PROXY_KEY
./codex-model-picker-policy --timeout 60
```

These flags do not change Codex's inference provider or persist connection
settings. `--source proxy` requires an endpoint; `--source codex` explicitly uses
unpinned Codex discovery. With no custom endpoint, the default `--source auto`
uses Codex discovery. A configured endpoint's request failure never falls back
to a bundled inventory.

## Standard model lists and rich catalogs

The endpoint's inventory determines catalog membership. Bundled models are never
added unless the endpoint advertises them.

- Standard OpenAI-compatible `GET /models` responses (`data[].id`) are supported.
- If the endpoint also provides Codex metadata via `?client_version=...`, that
  metadata enriches matching IDs. This preserves CLIProxyAPI's model instructions,
  reasoning choices, context limits, and other capabilities.
- Missing rich metadata is filled from an **exact model ID** in the installed
  Codex bundle, or a generic entry for a new model. No fuzzy alias matching.
- Endpoints returning a rich `models[].slug` catalog directly are also supported.

An unsupported rich-metadata request or a repeated standard response is normal;
authentication errors, network failures, and malformed rich responses remain
errors. Rich-only IDs absent from the standard inventory are ignored. A stale or
partial rich catalog does not remove extra IDs from the standard inventory.

Generic entries use original generic coding instructions, text input, and no
advertised reasoning choices or context limit unless the endpoint supplies them.
They are identified in the generated file's `_model_picker.generic_models` field.
Unknown context limits also mean no context-derived automatic compaction limit;
set Codex's `model_context_window` to the provider's documented limit when known.
Being listed does not prove that a model supports a successful Codex inference turn.

Output order is stable. Before replacement, the script asks the installed Codex
CLI to load the generated file in an isolated temporary home and verifies model
IDs and visibility. Files are written atomically with owner-only permissions.
Empty, duplicate, malformed, and explicitly paginated inventories are rejected
without replacing the last good catalog. The endpoint must return its full list.

## Picker exclusions

By default, the script shows advertised coding/chat models, including aliases,
GLM, and DeepSeek. It hides the internal `codex-auto-review` model and clearly
specialized image-generation, embedding, speech, moderation, realtime, and video
entries identified by known ID patterns or advertised metadata. Image/audio
**input** on a coding model does not hide it.

Unknown aliases cannot be classified from IDs alone. Add personal exclusions as
case-insensitive glob patterns:

```sh
./codex-model-picker-policy --exclude '*glm*' --exclude '*deepseek*'
```

Exclusions are saved in the generated file and reused on refresh and checks.
Providing `--exclude` replaces the saved patterns; `--clear-exclusions` clears
personal patterns while retaining the general suitability rules.

## Credentials and connectivity

Provider authentication supports `env_key`, `experimental_bearer_token`,
`http_headers`, and `env_http_headers`. Required missing keys fail before a request;
optional environment headers are omitted when unset. Provider `query_params` are
retained on both requests. Certificate configuration honors `CODEX_CA_CERTIFICATE`
and then `SSL_CERT_FILE`.

Credential helper commands and ChatGPT OAuth credentials for custom endpoints
are not supported; use `--api-key-env` when needed. Credentials and error response
bodies are not printed, and redirects are refused. Configuration values and
credentials are not embedded in the generated catalog.

## Install the executable

```sh
mkdir -p "$HOME/.local/bin"
install -m 755 ./codex-model-picker-policy "$HOME/.local/bin/codex-model-picker-policy"
```

Make sure `~/.local/bin` is on `PATH`.

## Tests

```sh
./tests/test.sh

# Also test the installed Codex against a local standard-only proxy.
CODEX_POLICY_REAL_CODEX=1 python3 tests/test_real_codex.py
```

The main suite additionally requires `jq` for the fake CLI and legacy checks.
Tests use temporary Codex homes, local HTTP fixtures, and placeholder credentials;
they do not read your real Codex configuration or contact your proxy.

The single executable keeps connection resolution (`CodexConfiguration`), endpoint
catalog discovery (`EndpointCatalog`), and picker exclusions separate. Tests
exercise their behavior through the command interface.
