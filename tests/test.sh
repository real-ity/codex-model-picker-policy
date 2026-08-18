#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_tmp=$(mktemp -d "${TMPDIR:-/tmp}/codex-model-policy-test.XXXXXX")
trap 'rm -rf "$test_tmp"' EXIT HUP INT TERM

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  file=$1
  expected=$2
  grep -F "$expected" "$file" >/dev/null || fail "$file does not contain: $expected"
}

test_install_preserves_config() {
  test_home="$test_tmp/install home"
  mkdir -p "$test_home"
  cp "$project_root/tests/fixtures/config.toml" "$test_home/config.toml"

  PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    "$project_root/codex-model-picker-policy" install >/dev/null

  assert_contains "$test_home/config.toml" 'model = "example-chat-model"'
  assert_contains "$test_home/config.toml" '[model_providers.example]'
  expected_override="model_catalog_json = \"$test_home/model-catalog.json\" # managed by codex-model-picker-policy"
  grep -F -x "$expected_override" "$test_home/config.toml" >/dev/null ||
    fail 'install did not write an exact TOML string path'
  [ -f "$test_home/model-catalog.json" ] || fail 'install did not create model-catalog.json'

  cp "$test_home/config.toml" "$test_home/config.after-first-install"
  backups_before=$(find "$test_home" -type f -name 'config.toml.bak.*' | wc -l | tr -d ' ')
  [ "$backups_before" = 1 ] || fail 'install did not create exactly one config backup'
  PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    "$project_root/codex-model-picker-policy" install >/dev/null
  backups_after=$(find "$test_home" -type f -name 'config.toml.bak.*' | wc -l | tr -d ' ')
  cmp -s "$test_home/config.after-first-install" "$test_home/config.toml" ||
    fail 'repeated install changed config.toml'
  [ "$backups_before" = "$backups_after" ] ||
    fail 'repeated install created a needless config backup'
}

test_refresh_bypasses_pin_and_keeps_internal_models_hidden() {
  test_home=$test_tmp/refresh-home
  mkdir -p "$test_home"
  cp "$project_root/tests/fixtures/pinned-config.toml" "$test_home/config.toml"
  cp "$test_home/config.toml" "$test_home/config.before"

  PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    CODEX_POLICY_REQUIRE_UNPINNED=1 \
    "$project_root/codex-model-picker-policy" refresh >/dev/null

  cmp -s "$test_home/config.before" "$test_home/config.toml" ||
    fail 'refresh changed config.toml'
  jq -e '
    [.models[] | {slug, visibility}] == [
      {"slug":"gpt-5.6-sol","visibility":"list"},
      {"slug":"deepseek/deepseek-v4-pro","visibility":"hide"},
      {"slug":"Unsloth/GLM-4.7-Flash-GUFF","visibility":"hide"},
      {"slug":"codex-auto-review","visibility":"hide"},
      {"slug":"gpt-image-1","visibility":"list"}
    ]
  ' "$test_home/model-catalog.json" >/dev/null ||
    fail 'refresh did not apply the visibility policy'
}

test_check_reports_counts_and_rejects_policy_drift() {
  test_home=$test_tmp/check-home
  mkdir -p "$test_home"
  cp "$project_root/tests/fixtures/config.toml" "$test_home/config.toml"

  PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    "$project_root/codex-model-picker-policy" install >/dev/null

  check_output=$(PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    "$project_root/codex-model-picker-policy" check)
  printf '%s\n' "$check_output" | grep -F 'total=5 visible=2 hidden=3' >/dev/null ||
    fail 'check did not report expected totals'

  jq '(.models[] | select(.slug == "gpt-5.6-sol")).visibility = "hide"' \
    "$test_home/model-catalog.json" >"$test_home/drifted.json"
  mv "$test_home/drifted.json" "$test_home/model-catalog.json"
  if PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    "$project_root/codex-model-picker-policy" check >/dev/null 2>&1; then
    fail 'check accepted a non-GLM/DeepSeek model marked hidden'
  fi
}

test_remove_deletes_only_managed_override() {
  test_home=$test_tmp/remove-home
  mkdir -p "$test_home"
  cp "$project_root/tests/fixtures/config.toml" "$test_home/config.toml"

  PATH="$project_root/tests/bin:$PATH" \
    CODEX_HOME="$test_home" \
    CODEX_POLICY_TEST_ROOT="$project_root" \
    "$project_root/codex-model-picker-policy" install >/dev/null
  cp "$test_home/model-catalog.json" "$test_home/catalog.before"

  CODEX_HOME="$test_home" "$project_root/codex-model-picker-policy" remove >/dev/null
  if grep -F '# managed by codex-model-picker-policy' "$test_home/config.toml" >/dev/null; then
    fail 'remove left the managed override in config.toml'
  fi
  assert_contains "$test_home/config.toml" 'model = "example-chat-model"'
  assert_contains "$test_home/config.toml" '[model_providers.example]'
  cmp -s "$test_home/catalog.before" "$test_home/model-catalog.json" ||
    fail 'remove changed or deleted the generated catalog'

  cp "$test_home/config.toml" "$test_home/config.after-first-remove"
  CODEX_HOME="$test_home" "$project_root/codex-model-picker-policy" remove >/dev/null
  cmp -s "$test_home/config.after-first-remove" "$test_home/config.toml" ||
    fail 'repeated remove was not idempotent'

  cp "$project_root/tests/fixtures/pinned-config.toml" "$test_home/config.toml"
  cp "$test_home/config.toml" "$test_home/unmanaged.before"
  CODEX_HOME="$test_home" "$project_root/codex-model-picker-policy" remove >/dev/null
  cmp -s "$test_home/unmanaged.before" "$test_home/config.toml" ||
    fail 'remove changed an unmanaged model_catalog_json setting'
}

test_install_preserves_config
printf '%s\n' 'PASS: install preserves unrelated config'
test_refresh_bypasses_pin_and_keeps_internal_models_hidden
printf '%s\n' 'PASS: refresh bypasses the pin and keeps internal models hidden'
test_check_reports_counts_and_rejects_policy_drift
printf '%s\n' 'PASS: check reports counts and rejects policy drift'
test_remove_deletes_only_managed_override
printf '%s\n' 'PASS: remove deletes only the managed override'
