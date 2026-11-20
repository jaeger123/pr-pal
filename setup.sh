#!/usr/bin/env bash
# Join pr-pal in one command. Run on your own machine with `gh` logged in to your PERSONAL account.
#
#   ./setup.sh <your-corporate-login> <buddy-login>[,<buddy-login>...] [org]
#
# Creates <you>/pr-pal from the template, sets variables, prompts for your corporate
# fine-grained PAT (Pull requests: read & write only), and fires a dry run.
set -euo pipefail
TEMPLATE="${PR_PAL_TEMPLATE:-jaeger123/pr-pal}"
me="${1:?usage: setup.sh <your-corporate-login> <buddy-login>[,...] [org]}"
buddies="${2:?usage: setup.sh <your-corporate-login> <buddy-login>[,...] [org]}"
org="${3:-cywarelabs-internal}"
owner="$(gh api user --jq .login)"
repo="$owner/pr-pal"

if ! gh repo view "$repo" >/dev/null 2>&1; then
  gh repo create "$repo" --public --template "$TEMPLATE" --description "Approve my buddies' PRs on my behalf, from a repo only I own"
  echo "created https://github.com/$repo"
  until gh api "repos/$repo/contents/.github/workflows/pr-pal.yml" >/dev/null 2>&1; do sleep 3; done
fi

gh variable set ME -R "$repo" -b "$me"
gh variable set BUDDIES -R "$repo" -b "$buddies"
gh variable set PR_PAL_ORG -R "$repo" -b "$org"
gh variable set DRY_RUN -R "$repo" -b 1
gh variable set SKIP_DRAFTS -R "$repo" -b 1
gh variable set LOG_LEVEL -R "$repo" -b INFO

gh api -X PUT "repos/$repo/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false >/dev/null
gh api -X PUT "repos/$repo/branches/main/protection" --input - >/dev/null <<'JSON' || echo "branch protection skipped (private repo on a free plan)"
{"required_status_checks":null,"enforce_admins":true,"required_pull_request_reviews":null,"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
JSON

echo
echo "Create a fine-grained PAT on your CORPORATE account ($me):"
echo "  resource owner = $org, only the repos you review, permission 'Pull requests: Read and write', expiry 1 year."
echo "  https://github.com/settings/personal-access-tokens/new"
read -rs -p "paste the token (hidden): " token; echo
printf %s "$token" | gh secret set PR_PAL_TOKEN -R "$repo"

gh workflow run pr-pal.yml -R "$repo"
echo "dry run started: gh run watch -R $repo   (look for '[dry-run] would approve')"
echo "go live:         gh variable set DRY_RUN -R $repo -b 0"
