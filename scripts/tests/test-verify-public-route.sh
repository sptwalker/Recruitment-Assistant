#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
verify_script="${script_dir}/../verify-public-route.sh"
test_root=$(mktemp -d)
trap 'rm -rf "${test_root}"' EXIT HUP INT TERM

mock_bin="${test_root}/bin"
mkdir -p "${mock_bin}"

cat > "${mock_bin}/kubectl" <<'MOCK_KUBECTL'
#!/bin/sh
case "$*" in
  *'get ingress'*'.pathType'*) printf 'Prefix\n' ;;
  *'get ingress'*'.backend.service.name'*) printf 'recruitment-assistant\n' ;;
  *'get ingress'*'.spec.rules[*].http.paths[*]'*) printf '/recruitment-assistant\n' ;;
  *'get ingress'*'.spec.rules[*]'*) printf 'hr.youdoogo.com\n' ;;
  *'get service'*'.spec.ports[*]'*) printf '8080\n' ;;
  *'get endpoints'*) printf '10.0.0.42\n' ;;
  *) printf 'unexpected kubectl invocation: %s\n' "$*" >&2; exit 2 ;;
esac
MOCK_KUBECTL
chmod 0755 "${mock_bin}/kubectl"

cat > "${mock_bin}/curl" <<'MOCK_CURL'
#!/bin/sh
set -eu

output_file=
header_file=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output|--dump-header|--write-out|--max-redirs|--connect-timeout|--max-time)
      option=$1
      shift
      value=$1
      case "${option}" in
        --output) output_file=${value} ;;
        --dump-header) header_file=${value} ;;
      esac
      ;;
    --silent|--show-error|--location) ;;
    http://*|https://*) url=$1 ;;
    *) printf 'unexpected curl argument: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

: "${MOCK_SCENARIO:?}"
: "${MOCK_STATE_FILE:?}"
: "${output_file:?}"
: "${header_file:?}"
: "${url:?}"

count=0
if [ -f "${MOCK_STATE_FILE}" ]; then
  count=$(cat "${MOCK_STATE_FILE}")
fi
count=$((count + 1))
printf '%s\n' "${count}" > "${MOCK_STATE_FILE}"

status=200
content_type='text/plain'
body='ok'

case "${url}" in
  */recruitment-assistant/)
    content_type='text/html; charset=utf-8'
    body='<!doctype html><html><head><script type="module" src="./static/js/index.abc123.js"></script></head></html>'
    case "${MOCK_SCENARIO}" in
      transient_404)
        if [ "${count}" -eq 1 ]; then status=404; body='not found'; fi
        ;;
      persistent_failure) status=404; body='not found' ;;
    esac
    ;;
  */recruitment-assistant/_stcore/health)
    content_type='text/plain'
    body='ok'
    ;;
  */recruitment-assistant/static/js/index.abc123.js)
    case "${MOCK_SCENARIO}" in
      html_asset_fallback)
        content_type='text/html; charset=utf-8'
        body='<!doctype html><html><body>fallback</body></html>'
        ;;
      *)
        content_type='application/javascript'
        body='console.log("streamlit");'
        ;;
    esac
    ;;
  *) status=404; content_type='text/plain'; body='unexpected URL' ;;
esac

printf 'HTTP/2 %s\r\nContent-Type: %s\r\n\r\n' "${status}" "${content_type}" > "${header_file}"
printf '%s\n' "${body}" > "${output_file}"
printf '%s' "${status}"
MOCK_CURL
chmod 0755 "${mock_bin}/curl"

run_case() {
  name=$1
  scenario=$2
  expected=$3
  state_file="${test_root}/${name}.state"
  output_file="${test_root}/${name}.output"

  set +e
  PATH="${mock_bin}:${PATH}" \
    MOCK_SCENARIO="${scenario}" \
    MOCK_STATE_FILE="${state_file}" \
    PUBLIC_VERIFY_ATTEMPTS=3 \
    PUBLIC_VERIFY_DELAY_SECONDS=0 \
    sh "${verify_script}" > "${output_file}" 2>&1
  actual=$?
  set -e

  if [ "${expected}" = "success" ] && [ "${actual}" -ne 0 ]; then
    printf 'FAIL %s: expected success, got %s\n' "${name}" "${actual}" >&2
    cat "${output_file}" >&2
    exit 1
  fi
  if [ "${expected}" = "failure" ] && [ "${actual}" -eq 0 ]; then
    printf 'FAIL %s: expected failure\n' "${name}" >&2
    cat "${output_file}" >&2
    exit 1
  fi
  printf 'PASS %s\n' "${name}"
}

run_case success success success
run_case transient_elb_404 transient_404 success
run_case persistent_route_failure persistent_failure failure
run_case html_static_asset_fallback html_asset_fallback failure
