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
FUELV_WRITE_KEY=replace_with_your_uuid_write_key
R2_ACCOUNT_ID=d2fe1099fc2a477abb4fd83b85d0cfe6
R2_ACCESS_KEY_ID=replace_with_your_r2_access_key_id
R2_SECRET_ACCESS_KEY=replace_with_your_r2_secret_access_key
R2_BUCKET_NAME=public-bucket
R2_KEY_PREFIX=fvs
R2_PUBLIC_BASE_URL=https://r2.pritabrata.com
EOF
	chmod 600 "$env_file"
	printf 'Created %s — edit it to set your GEMINI_API_KEY, FUELV_WRITE_KEY, and R2 credentials before running GitHub Actions deploy.\n' "$env_file"
else
	printf 'Existing %s found.\n' "$env_file"
fi
