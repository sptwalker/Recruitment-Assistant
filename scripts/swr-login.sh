#!/bin/sh
set -eu

: "${SWR_REGION:?SWR_REGION must be configured in GitLab CI variables}"
: "${SWR_AK:?SWR_AK must be configured in GitLab CI variables}"
: "${SWR_PASSWORD:?SWR_PASSWORD must be configured in GitLab CI variables}"

SWR_ENDPOINT="swr.${SWR_REGION}.myhuaweicloud.com"
printf '%s' "${SWR_PASSWORD}" | docker login \
  --username "${SWR_REGION}@${SWR_AK}" \
  --password-stdin \
  "${SWR_ENDPOINT}"

export SWR_REGISTRY="${SWR_ENDPOINT}/nexus-studio"
printf 'Authenticated to %s\n' "${SWR_ENDPOINT}"
