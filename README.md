# Portable Codex model-picker policy

This small POSIX shell utility makes every discovered Codex model picker-visible
except model IDs containing `glm` or `deepseek`, case-insensitively. That literal
rule also exposes special entries such as image models and `codex-auto-review`.

It generates the catalog on each destination machine instead of copying a catalog
from another installation. This keeps the result aligned with that machine's Codex
version, account, provider, and available models.

## Requirements

- macOS, Linux, or WSL with a POSIX shell
- `codex` with the `codex debug models` command
- `jq`

The utility honors `CODEX_HOME`; otherwise it uses `$HOME/.codex`, Codex's normal
user configuration directory. Codex documents `model_catalog_json` as an optional
startup catalog path in its [configuration reference](https://developers.openai.com/codex/config-reference/).

## Install the utility

From this directory:

```sh
mkdir -p "$HOME/.local/bin"
install -m 755 ./codex-model-picker-policy "$HOME/.local/bin/codex-model-picker-policy"
```

Make sure `$HOME/.local/bin` is on `PATH`, then apply the policy:

```sh
codex-model-picker-policy install
codex-model-picker-policy check
```

`install` first discovers and validates a live catalog, atomically writes
`$CODEX_HOME/model-catalog.json`, and then adds or updates this root setting in
`config.toml`:

```toml
model_catalog_json = "/absolute/path/to/model-catalog.json" # managed by codex-model-picker-policy
```

Existing `config.toml` content is preserved. The utility creates a timestamped
backup before changing it and does not create another backup on an unchanged,
repeated install.

Restart the Codex app or IDE after installing or refreshing so its model picker
reloads the startup catalog.

## Commands

```text
codex-model-picker-policy install
codex-model-picker-policy refresh
codex-model-picker-policy check
codex-model-picker-policy remove
```

- `install` refreshes the catalog and idempotently applies the config override.
- `refresh` rediscovers the live catalog and atomically replaces the generated
  catalog without changing `config.toml`.
- `check` prints total, visible, and hidden counts and fails unless every model's
  visibility exactly matches the policy. It checks both the file and the catalog
  rendered by the CLI. On macOS it also checks executable Codex binaries found in
  installed Codex or ChatGPT app bundles.
- `remove` removes only the marked, managed config entry. It retains the generated
  catalog and leaves an unmarked `model_catalog_json` entry untouched.

## Refresh behavior and secrets

Once configured, `model_catalog_json` pins the startup catalog. To bypass that pin,
`refresh` creates a mode-700 temporary Codex home, writes a mode-600 temporary copy
of `config.toml` with only the root `model_catalog_json` entry removed, and links the
existing `auth.json` when present. It never prints configuration or credential
values. Cleanup runs on normal exit and common termination signals.

If discovery, JSON validation, or transformation fails, the existing managed
catalog is left untouched.

## Test

Tests use isolated temporary Codex homes, placeholder-only fixtures, and a fake
`codex` executable; they never read the real Codex configuration or credentials.

```sh
./tests/test.sh
```
