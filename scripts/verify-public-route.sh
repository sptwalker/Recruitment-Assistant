#!/bin/sh
set -eu

namespace="${CCE_NAMESPACE:-recruitment-assistant}"
ingress_name="${CCE_INGRESS_NAME:-recruitment-assistant}"
service_name="${CCE_SERVICE_NAME:-recruitment-assistant}"
service_port="${CCE_SERVICE_PORT:-8080}"
public_origin="${PUBLIC_ORIGIN:-https://hr.youdoogo.com}"
route_path="${PUBLIC_ROUTE_PATH:-/recruitment-assistant}"
attempt_limit="${PUBLIC_VERIFY_ATTEMPTS:-12}"
retry_delay="${PUBLIC_VERIFY_DELAY_SECONDS:-5}"
curl_bin="${CURL_BIN:-curl}"
kubectl_bin="${KUBECTL_BIN:-kubectl}"

case "${route_path}" in
  /*/) route_path=${route_path%/} ;;
  /*) ;;
  *) printf 'PUBLIC_ROUTE_PATH must start with /: %s\n' "${route_path}" >&2; exit 2 ;;
esac

base_url="${public_origin}${route_path}/"
health_url="${public_origin}${route_path}/_stcore/health"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT HUP INT TERM

require_exact_line() {
  expected=$1
  description=$2
  shift 2
  output=$("$@") || {
    printf 'Failed to inspect %s.\n' "${description}" >&2
    return 1
  }
  if ! printf '%s\n' "${output}" | grep -F -x -- "${expected}" >/dev/null; then
    printf 'Expected %s %s, got: %s\n' "${description}" "${expected}" "${output}" >&2
    return 1
  fi
}

require_nonempty_line() {
  description=$1
  shift
  output=$("$@") || {
    printf 'Failed to inspect %s.\n' "${description}" >&2
    return 1
  }
  if ! printf '%s\n' "${output}" | grep -E '[^[:space:]]' >/dev/null; then
    printf '%s is empty.\n' "${description}" >&2
    return 1
  fi
}

require_exact_line "hr.youdoogo.com" "Ingress host" \
  "${kubectl_bin}" get ingress "${ingress_name}" --namespace "${namespace}" \
  -o 'jsonpath={range .spec.rules[*]}{.host}{"\n"}{end}'
require_exact_line "${route_path}" "Ingress path" \
  "${kubectl_bin}" get ingress "${ingress_name}" --namespace "${namespace}" \
  -o 'jsonpath={range .spec.rules[*].http.paths[*]}{.path}{"\n"}{end}'
require_exact_line "Prefix" "Ingress path type" \
  "${kubectl_bin}" get ingress "${ingress_name}" --namespace "${namespace}" \
  -o 'jsonpath={range .spec.rules[*].http.paths[*]}{.pathType}{"\n"}{end}'
require_exact_line "${service_name}" "Ingress backend service" \
  "${kubectl_bin}" get ingress "${ingress_name}" --namespace "${namespace}" \
  -o 'jsonpath={range .spec.rules[*].http.paths[*]}{.backend.service.name}{"\n"}{end}'
require_exact_line "${service_port}" "Service port" \
  "${kubectl_bin}" get service "${service_name}" --namespace "${namespace}" \
  -o 'jsonpath={range .spec.ports[*]}{.port}{"\n"}{end}'
require_nonempty_line "Service endpoints" \
  "${kubectl_bin}" get endpoints "${service_name}" --namespace "${namespace}" \
  -o 'jsonpath={range .subsets[*].addresses[*]}{.ip}{"\n"}{end}'

http_get() {
  url=$1
  body_file=$2
  header_file=$3
  "${curl_bin}" \
    --silent \
    --show-error \
    --location \
    --max-redirs 3 \
    --connect-timeout 5 \
    --max-time 10 \
    --output "${body_file}" \
    --dump-header "${header_file}" \
    --write-out '%{http_code}' \
    "${url}"
}

extract_asset_reference() {
  grep -E -o '(src|href)="[^"]*static/[^"]+"' "$1" \
    | sed -n '1{s/^[^=]*="//;s/"$//;p;}'
}

resolve_asset_url() {
  asset_reference=$1
  case "${asset_reference}" in
    "${base_url}"*) printf '%s\n' "${asset_reference}" ;;
    http://*|https://*) return 1 ;;
    "${route_path}/"*) printf '%s%s\n' "${public_origin}" "${asset_reference}" ;;
    /*) return 1 ;;
    ./*) printf '%s%s\n' "${base_url}" "${asset_reference#./}" ;;
    ../*|*'/../'*) return 1 ;;
    *) printf '%s%s\n' "${base_url}" "${asset_reference}" ;;
  esac
}

attempt=1
while [ "${attempt}" -le "${attempt_limit}" ]; do
  root_body="${work_dir}/root-${attempt}.body"
  root_headers="${work_dir}/root-${attempt}.headers"
  root_status=$(http_get "${base_url}" "${root_body}" "${root_headers}" 2>/dev/null || printf '000')

  if [ "${root_status}" = "200" ]; then
    asset_reference=$(extract_asset_reference "${root_body}" || true)
    asset_url=$(resolve_asset_url "${asset_reference}" 2>/dev/null || true)
    health_body="${work_dir}/health-${attempt}.body"
    health_headers="${work_dir}/health-${attempt}.headers"
    health_status=$(http_get "${health_url}" "${health_body}" "${health_headers}" 2>/dev/null || printf '000')

    if [ -n "${asset_url}" ] && [ "${health_status}" = "200" ] \
      && grep -E -x '[[:space:]]*ok[[:space:]]*' "${health_body}" >/dev/null; then
      asset_body="${work_dir}/asset-${attempt}.body"
      asset_headers="${work_dir}/asset-${attempt}.headers"
      asset_status=$(http_get "${asset_url}" "${asset_body}" "${asset_headers}" 2>/dev/null || printf '000')
      if [ "${asset_status}" = "200" ] \
        && ! grep -i -E '^content-type:[[:space:]]*text/html' "${asset_headers}" >/dev/null \
        && ! grep -i -E '<!doctype html|<html([[:space:]>])' "${asset_body}" >/dev/null; then
        printf 'Public route verified: %s (health and base-scoped asset OK)\n' "${base_url}"
        exit 0
      fi
    fi
  fi

  printf 'Public route attempt %s/%s failed (root HTTP %s).\n' \
    "${attempt}" "${attempt_limit}" "${root_status}" >&2
  if [ "${attempt}" -lt "${attempt_limit}" ]; then
    sleep "${retry_delay}"
  fi
  attempt=$((attempt + 1))
done

printf 'Public route verification failed after %s attempts: %s\n' \
  "${attempt_limit}" "${base_url}" >&2
exit 1
