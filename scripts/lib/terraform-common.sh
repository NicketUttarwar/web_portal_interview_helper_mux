#!/usr/bin/env bash
# Shared helpers for Terraform wrappers. Source after SCRIPT_DIR is set to scripts/.

: "${SCRIPT_DIR:?terraform-common: SCRIPT_DIR must be set}"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"
TF_STATE_LIVE="${TF_DIR}/state/terraform.tfstate"

if [[ -z "${TF_SCRIPT_NAME:-}" ]]; then
  if [[ -n "${BASH_SOURCE[1]:-}" ]]; then
    TF_SCRIPT_NAME="$(basename "${BASH_SOURCE[1]}")"
  else
    TF_SCRIPT_NAME="terraform"
  fi
fi

tf_log() {
  echo "[$TF_SCRIPT_NAME] $*" >&2
}

tf_warn() {
  echo "[$TF_SCRIPT_NAME] WARNING: $*" >&2
}

terraform_common_require() {
  if ! command -v terraform >/dev/null 2>&1; then
    tf_warn "terraform is not installed or not in PATH"
    exit 127
  fi
  if [[ ! -f "$TF_DIR/versions.tf" ]]; then
    tf_warn "Terraform directory appears invalid (missing versions.tf): $TF_DIR"
    exit 1
  fi
}

terraform_common_source_aws_env() {
  local env="${REPO_ROOT}/config/secrets/secrets.env"
  if [[ ! -f "$env" ]]; then
    tf_warn "Missing $env"
    exit 1
  fi
  tf_log "Loading AWS environment from config/secrets/secrets.env"
  set -a
  # shellcheck source=/dev/null
  source "$env"
  set +a
  if [[ -z "${AWS_SESSION_TOKEN:-}" ]]; then
    unset AWS_SESSION_TOKEN
  fi
  if [[ -z "${AWS_PROFILE:-}" ]]; then
    unset AWS_PROFILE
  fi
}

terraform_common_effective_var_file_path() {
  if [[ -f "${REPO_ROOT}/config/terraform.tfvars" ]]; then
    printf '%s' "${REPO_ROOT}/config/terraform.tfvars"
    return 0
  fi
  return 1
}

terraform_common_subcommand_needs_var_file() {
  case "${1:-}" in
    plan | apply | destroy | refresh | console | import)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

terraform_common_exec() {
  terraform_common_require
  terraform_common_source_aws_env
  local -a args=("$@")
  local subcmd="${args[0]:-}"
  local vf=""
  vf=$(terraform_common_effective_var_file_path) || vf=""
  if [[ -n "$vf" ]] && terraform_common_subcommand_needs_var_file "$subcmd"; then
    tf_log "Using -var-file=$vf"
    args+=(-var-file="$vf")
  fi
  tf_log "terraform -chdir=$TF_DIR ${args[*]}"
  (cd "$REPO_ROOT" && terraform -chdir="$TF_DIR" "${args[@]}")
}
