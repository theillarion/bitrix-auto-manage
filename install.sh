#!/usr/bin/env bash
#
# Install bitrix-auto-manage from the prebuilt image on ghcr.io.
#
# Downloads into the CURRENT directory (never overwrites existing files):
#   docker-compose.yml   <- docker-compose.ghcr.yml   (image, no build)
#   config/schedule.yaml <- config/schedule.example.yaml
#   .env                 <- .env.example
# Also creates the config/ and state/ directories (idempotent).
#
# Usage:
#   ./install.sh [ref]     # ref: git tag or branch; default: latest release tag
#
# The body of the script is POSIX sh (works under dash/busybox ash as well),
# so it also runs as a one-liner (see README, section 2.1.1):
#   sh -c "$(curl -fsSL https://raw.githubusercontent.com/theillarion/bitrix-auto-manage/master/install.sh)" [sh [ref]]

set -eu

REPO="theillarion/bitrix-auto-manage"
LATEST_API="https://api.github.com/repos/${REPO}/releases/latest"

say()    { printf '%s\n' "$*"; }
notice() { printf 'install.sh: notice: %s\n' "$*"; }
error()  { printf 'install.sh: error: %s\n' "$*" >&2; }
die()    { error "$*"; exit 1; }

command -v curl >/dev/null 2>&1 \
  || die "curl is required but not found in PATH"

# --- Docker with Compose v2 must already be installed (fail fast) -----------
# The script never installs anything and never runs get.docker.com itself.

command -v docker >/dev/null 2>&1 || die "Docker is required but not found \
in PATH. Install Docker (with Compose v2): https://docs.docker.com/engine/install/ \
— on Linux you may also review and run the official convenience script \
yourself: curl -fsSL https://get.docker.com | sh"

# `docker compose version` is a client-side command (unlike `docker version`,
# it does not talk to the daemon); it fails when the Compose v2 plugin is
# missing. The compose file of this project requires Compose v2.24+.
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required \
but not available ('docker compose' subcommand not found). Install/upgrade \
Docker: https://docs.docker.com/engine/install/ (the compose file of this \
project requires Compose v2.24+)"

# --- resolve ref: argument, or the latest release tag from the GitHub API ---

REF="${1:-}"
if [ -z "$REF" ]; then
  say "No ref given; resolving the latest release tag via the GitHub API..."
  # No `set -o pipefail` here (it is not POSIX sh): the exit code of curl is
  # checked explicitly, and the API response goes into a variable first —
  # there are no `curl | sed` pipelines anywhere in this script.
  API_RESPONSE="$(curl -fsSL "$LATEST_API" 2>/dev/null)" || API_RESPONSE=""
  if [ -n "$API_RESPONSE" ]; then
    # The releases/latest response contains a single "tag_name" field.
    REF="$(printf '%s\n' "$API_RESPONSE" \
      | sed -n 's/^.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*$/\1/p' \
      | sed -n '1p')" || REF=""
  fi
  [ -n "$REF" ] || die "cannot resolve the latest release tag (GitHub API \
unreachable or unexpected response); pass a ref explicitly: ./install.sh v0.4.1"
fi

# --- validate ref BEFORE any URL is built (URL injection guard) ------------

# The ref becomes a path segment of a raw.githubusercontent.com URL: only
# tag/branch-safe characters are allowed, so it can never alter the host,
# the scheme, or escape into another repository's path.
case "$REF" in
  *[!A-Za-z0-9._-]*)
    die "invalid ref '${REF}': expected a git tag or branch name (allowed \
characters: letters, digits, '.', '_', '-')"
    ;;
esac

BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
say "Using ref: ${REF}"

# --- directories for the mounted volumes (idempotent) ----------------------

mkdir -p config state

# --- downloads --------------------------------------------------------------

TMP_FILE=""
cleanup() { [ -z "$TMP_FILE" ] || rm -f "$TMP_FILE"; }
trap cleanup EXIT

# fetch <url> <destination>: download to a temp file first; an existing
# destination is preserved as is, a failed download leaves nothing behind.
# Exit codes are checked explicitly (POSIX sh has no `set -o pipefail`).
fetch() {
  fetch_url="$1"
  fetch_dest="$2"
  if [ -e "$fetch_dest" ]; then
    notice "preserved existing ${fetch_dest} (not overwritten)"
    return 0
  fi
  TMP_FILE="${fetch_dest}.tmp.$$"
  if curl -fsSL "$fetch_url" -o "$TMP_FILE"; then
    mv "$TMP_FILE" "$fetch_dest"
    TMP_FILE=""
    say "downloaded ${fetch_dest}"
  else
    error "failed to download ${fetch_url}"
    return 1
  fi
}

fetch "${BASE}/docker-compose.ghcr.yml" ./docker-compose.yml
fetch "${BASE}/config/schedule.example.yaml" ./config/schedule.yaml
fetch "${BASE}/.env.example" ./.env

# --- next steps -------------------------------------------------------------

say ""
say "Done. Next steps:"
say "  1. Put your webhook into .env:"
say "       BITRIX_WEBHOOK_URL=https://<portal>/rest/<USER_ID>/<secret>/"
say "  2. Edit config/schedule.yaml (days, times, contracts — see docs/setup.md"
say "     in the repository)."
say "  3. Check the webhook and the portal schema (read-only):"
say "       docker compose run --rm bitrix-auto check"
say "  4. Preview the plan (no writes):"
say "       docker compose run --rm bitrix-auto preview --config config/schedule.yaml --days 3"
say "  5. Start the daemon:"
say "       docker compose up -d"
say "  6. Follow the logs:"
say "       docker compose logs -f"
say ""
say "Update later:   docker compose pull && docker compose up -d"
say "Pin a version:  set BITRIX_AUTO_TAG=X.Y.Z in .env (default: latest)."
