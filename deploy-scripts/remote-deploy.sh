#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

for name in DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS FUELV_IMAGE GITHUB_SHA GITHUB_RUN_ID GITHUB_RUN_ATTEMPT GITHUB_RUN_NUMBER GHCR_USERNAME GHCR_TOKEN; do
	[[ -n "${!name:-}" ]] || { printf 'Missing %s\n' "$name" >&2; exit 1; }
done
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]
[[ "$DEPLOY_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]
[[ "$GHCR_USERNAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]]
for value in "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$GITHUB_RUN_NUMBER"; do [[ "$value" =~ ^[0-9]+$ ]]; done
[[ "$FUELV_IMAGE" =~ ^ghcr.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$work/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$work/known_hosts"
unset DEPLOY_SSH_KEY
ssh_args=(-i "$work/key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$work/known_hosts" -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
target="$DEPLOY_USER@$DEPLOY_HOST"
release_dir="fuelv-deploy/releases/$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
printf 'FUELV_IMAGE=%s\n' "$FUELV_IMAGE" > "$work/images.env"
tar -czf "$work/release.tar.gz" compose.yaml deploy-scripts -C "$work" images.env
ssh "${ssh_args[@]}" "$target" "umask 077; mkdir -p '$release_dir'; cat > '$release_dir/release.tar.gz'" < "$work/release.tar.gz"
printf '%s\n' "$GHCR_TOKEN" | ssh "${ssh_args[@]}" "$target" "cd '$release_dir' && tar -xzf release.tar.gz && read -r registry_token && { printf '%s\\n' \"\$registry_token\" | nohup bash deploy-scripts/registry-deploy.sh '$GITHUB_SHA' '$GITHUB_RUN_NUMBER' '$GHCR_USERNAME' > deploy.log 2>&1 & }"
unset GHCR_TOKEN

for ((attempt = 0; attempt < 120; attempt++)); do
	status="$(ssh "${ssh_args[@]}" "$target" "if test -f '$release_dir/result'; then cat '$release_dir/result'; else echo running; fi")" || status=running
	if [[ "$status" != running ]]; then
		ssh "${ssh_args[@]}" "$target" "cat '$release_dir/deploy.log'" || true
		[[ "$status" == 0 ]]
		exit
	fi
	sleep 10
done
printf 'Timed out waiting for deployment. Inspect ~/%s/deploy.log before retrying.\n' "$release_dir" >&2
exit 1
