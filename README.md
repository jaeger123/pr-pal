# pr-pal

Approves your buddies' pull requests on your behalf, every 5 minutes, from a repository that **only you** own. Your token never leaves GitHub, and nobody at your company, including org owners and cluster admins, can read it: repository secrets cannot be read back, and nobody but you can push a workflow change to your copy.

Everyone who joins hosts their own copy with their own token and their own buddy list. Your copy approves their PRs; theirs approve yours.

## Join in one command

On your machine, with `gh` logged in to your **personal** GitHub account:

```sh
curl -fsSL https://raw.githubusercontent.com/jaeger123/pr-pal/main/setup.sh -o setup.sh
bash setup.sh <your-corporate-login> <buddy-login>[,<buddy-login>...]
```

It creates `<you>/pr-pal` from this template, sets the variables, asks for your corporate token without echoing it, and fires a dry run. Then tell your buddies your corporate login so they add you to their `BUDDIES`.

## Manual setup
1. Create a fine-grained personal access token on your **corporate** account: resource owner = your org, only the repositories you review, permission **Pull requests: Read and write**, expiry 1 year. Nothing else.
2. Click **Use this template** to copy this repository into your **personal** account. Keep it public (unlimited free Actions minutes) with zero collaborators.
3. Settings → Secrets and variables → Actions:
   - Secret `PR_PAL_TOKEN` = the token from step 1.
   - Variables: `ME` = your corporate login, `BUDDIES` = comma-separated buddy logins, `PR_PAL_ORG` = the org, `DRY_RUN` = `1` to start, `SKIP_DRAFTS` = `1`, `LOG_LEVEL` = `INFO`.
4. Settings → Actions → General: "Allow all actions" is not needed; this workflow uses none. Set workflow permissions to **Read repository contents**. Enable "Require approval for all external contributors".
5. Settings → Branches: protect `main` so only you can push.
6. Run the workflow once from the Actions tab, read the `[dry-run] would approve` lines, then set `DRY_RUN` to `0`.

## Why this is safe in a public repository

- No third-party actions, so no supply chain to poison. The script is fetched from this repo at the exact commit that triggered the run.
- Secrets are never exposed to pull requests from forks, and `pull_request` triggers are not enabled at all.
- The workflow only runs on `schedule` and `workflow_dispatch`, both of which execute the default branch, which only you can change.
- The token can only read and review pull requests on the repos you chose. It cannot push, merge, or read code outside PR diffs.

## Latency

GitHub runs `schedule` at best every 5 minutes and sometimes late at the top of the hour; this workflow runs at minute 2, 7, 12 and so on to dodge that. For about 1 minute latency, something that is always on can hit `workflow_dispatch` every minute with a fine-grained PAT from your **personal** account scoped to this one repository with only **Actions: Read and write**. If that token leaks, the worst anyone can do is trigger or cancel a run.

## Keeping it alive

GitHub disables scheduled workflows in public repos after 60 days without repository activity. The `keepalive` workflow commits a timestamp to `.heartbeat` twice a month so that never happens.
