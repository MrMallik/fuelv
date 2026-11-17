#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

release_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state_root="${FUELV_DEPLOY_ROOT:-$HOME/fuelv-deploy}"
rollback_needed=false
registry_config=""
finish() {
	local code=$?
	trap - EXIT INT TERM
	if [[ -n "$registry_config" ]]; then rm -rf -- "$registry_config"; fi
	unset registry_token
	if [[ "$rollback_needed" == true ]]; then
		printf 'Deployment failed; restoring previous fuelv container.\n' >&2
		if [[ -f "$release_root/previous.json" ]] && docker compose -p fuelv-production -f "$release_root/previous.json" up -d --no-build --pull never --wait --wait-timeout 180 && bash "$release_root/deploy-scripts/smoke.sh"; then
			printf 'Previous release restored and verified.\n' >&2
		else
			printf 'ROLLBACK FAILED: inspect containers immediately.\n' >&2
		fi
		code=1
	fi
	printf '%s\n' "$code" > "$release_root/result.tmp"
	mv "$release_root/result.tmp" "$release_root/result"
	exit "$code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $# == 3 && "$1" =~ ^[a-f0-9]{40}$ && "$2" =~ ^[0-9]+$ && "$3" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]
export RELEASE="$1"
sequence="$2"
registry_user="$3"
IFS= read -r registry_token
[[ -n "$registry_token" ]]
for command in docker curl python3 flock; do command -v "$command" >/dev/null; done
mkdir -p "$state_root"
exec 9> "$state_root/deploy.lock"
flock -w 60 9
[[ -f "$state_root/.env.production" ]] || { printf 'Create ~/fuelv-deploy/.env.production first; see deploy-scripts/setup-server.sh.\n' >&2; exit 1; }
if [[ -f "$state_root/sequence" ]] && (( sequence < $(cat "$state_root/sequence") )); then
	printf 'Refusing an older workflow run after a newer successful deployment.\n' >&2; exit 1;
fi
set -a
source "$release_root/images.env"
set +a
[[ "$FUELV_IMAGE" =~ ^ghcr.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]

compose=(docker compose --project-directory "$release_root" --env-file "$state_root/.env.production" -p fuelv-production -f "$release_root/compose.yaml")
"${compose[@]}" config --format json > "$release_root/candidate.json"
docker compose -p fuelv-production -f "$release_root/candidate.json" config --quiet

if [[ -f "$state_root/current.json" ]] && docker inspect fuelv >/dev/null 2>&1; then
	docker inspect fuelv > "$release_root/containers.json"
	python3 "$release_root/deploy-scripts/snapshot.py" "$state_root/current.json" "$release_root/containers.json" "$release_root/previous.json"
fi

registry_config="$(mktemp -d "$release_root/registry.XXXXXX")"
printf '%s' "$registry_token" | docker --config "$registry_config" login ghcr.io -u "$registry_user" --password-stdin
unset registry_token
docker --config "$registry_config" pull "$FUELV_IMAGE"
rm -rf -- "$registry_config"
registry_config=""

rollback_needed=true
docker compose -p fuelv-production -f "$release_root/candidate.json" up -d --no-build --pull never --wait --wait-timeout 180
bash "$release_root/deploy-scripts/smoke.sh"

if [[ -f "$release_root/previous.json" ]]; then
	cp "$release_root/previous.json" "$state_root/previous.json.tmp"
	mv "$state_root/previous.json.tmp" "$state_root/previous.json"
fi
cp "$release_root/candidate.json" "$state_root/current.json.tmp"
mv "$state_root/current.json.tmp" "$state_root/current.json"
rollback_needed=false
printf '%s\n' "$sequence" > "$state_root/sequence.tmp"
mv "$state_root/sequence.tmp" "$state_root/sequence"
printf 'Deployed fuelv release %s successfully.\n' "$RELEASE"
