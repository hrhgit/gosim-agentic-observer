#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
  echo "::error::Evaluation worker requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY repository secrets. Keep EVALUATION_WORKER_ENABLED=false until a coordinated backend handover."
  exit 1
fi
