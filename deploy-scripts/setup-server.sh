#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { printf 'This workflow requires a Linux x86_64 server.\n' >&2; exit 1; }
[[ "$EUID" != 0 ]] || { printf 'Run as your deployment SSH user, without sudo.\n' >&2; exit 1; }
for command in docker python3 curl flock; do
	command -v "$command" >/dev/null || { printf 'Missing required command: %s\n' "$command" >&2; exit 1; }
done
docker info >/dev/null
docker compose version

state_root="$HOME/fuelv-deploy"
mkdir -p "$state_root/releases"
chmod 700 "$state_root"

env_file="$state_root/.env.production"
if [[ ! -f "$env_file" ]]; then
	cat > "$env_file" <<'EOF'
EXTRACTOR_BACKEND=gemini
GEMINI_API_KEY=replace_with_your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
EOF
	chmod 600 "$env_file"
	printf 'Created %s — edit it to set your GEMINI_API_KEY before running GitHub Actions deploy.\n' "$env_file"
else
	printf 'Existing %s found.\n' "$env_file"
fi
