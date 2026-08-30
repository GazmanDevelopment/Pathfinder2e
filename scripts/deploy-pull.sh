#!/usr/bin/env bash
# Pulls the latest app code onto the box and rebuilds the image, without
# risking the two hand-edited, git-tracked-but-live-diverged config files
# (authelia/configuration.yml, authelia/users_database.yml). See
# docs/truenas-setup.md §10 for why this dance is necessary — briefly,
# skip-worktree alone isn't enough to make `git checkout`/`git pull` leave
# these files alone when the target branch's tracked content differs.
#
# Usage:
#   scripts/deploy-pull.sh              # pull the current branch (normal case)
#   scripts/deploy-pull.sh <branch>      # switch to <branch> AND fast-forward
#                                        # it to origin/<branch> (a local branch
#                                        # ref can already exist and be stale —
#                                        # checkout alone won't update it)
#
# Run this from anywhere inside the repo checkout.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONFIG_FILES=(authelia/configuration.yml authelia/users_database.yml)
BACKUP_DIR="$(mktemp -d)"
trap 'rm -rf "$BACKUP_DIR"' EXIT

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
  echo "==> Plain 'docker' isn't usable here; using 'sudo docker' instead."
fi

echo "==> Backing up live config files"
for f in "${CONFIG_FILES[@]}"; do
  cp "$f" "$BACKUP_DIR/$(basename "$f")"
done

echo "==> Un-marking skip-worktree so git can touch these files"
for f in "${CONFIG_FILES[@]}"; do
  git update-index --no-skip-worktree "$f"
done

echo "==> Discarding local (tracked-template) copies"
git checkout -- "${CONFIG_FILES[@]}"

echo "==> Fetching latest from origin"
git fetch origin

if [ "${1:-}" != "" ]; then
  echo "==> Switching to branch '$1'"
  git checkout "$1"
  echo "==> Fast-forwarding '$1' to origin/$1"
  git merge --ff-only "origin/$1"
else
  echo "==> Pulling latest on the current branch"
  git pull
fi

echo "==> Restoring live config files"
for f in "${CONFIG_FILES[@]}"; do
  cp "$BACKUP_DIR/$(basename "$f")" "$f"
done

echo "==> Re-marking skip-worktree"
for f in "${CONFIG_FILES[@]}"; do
  git update-index --skip-worktree "$f"
done

echo "==> Building the app image"
"${DOCKER[@]}" build -t pf2e-sheet:latest .

echo "==> Done. Redeploy (Apps UI restart, or 'docker compose up -d --build')"
echo "    to actually run the new image — this script only builds it."
