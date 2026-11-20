"""pr-pal poller: approve buddies' pull requests on each member's behalf, forever.

Tokens live as files in TOKEN_DIR, one file per GitHub login (file name = login,
content = that person's token). Every member is a buddy of every other member.
EXTRA_BUDDIES (comma list) adds logins whose PRs get approved without a token of their own.
Standard library only, so it runs on a stock python image.
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"
ORG = os.environ.get("GITHUB_ORG", "cywarelabs-internal")
TOKEN_DIR = Path(os.environ.get("TOKEN_DIR", "/secrets/tokens"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
SKIP_DRAFTS = os.environ.get("SKIP_DRAFTS", "1") == "1"
REVIEW_BODY = os.environ.get("REVIEW_BODY", "Approved via pr-pal.")
EXTRA_BUDDIES = {b.strip() for b in os.environ.get("EXTRA_BUDDIES", "").split(",") if b.strip()}
HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/pr-pal-heartbeat"))
MAX_PER_SEARCH = 100

log = logging.getLogger("pr-pal")


class RateLimited(Exception):
    def __init__(self, wait_seconds: float):
        super().__init__(f"rate limited, wait {wait_seconds:.0f}s")
        self.wait_seconds = wait_seconds


@dataclass
class Member:
    login: str
    token: str


def gh(token: str, method: str, path: str, body: dict | None = None) -> dict | list:
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "pr-pal-poller")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as err:
        if err.code in (403, 429):
            retry_after = err.headers.get("Retry-After")
            remaining = err.headers.get("X-RateLimit-Remaining")
            reset = err.headers.get("X-RateLimit-Reset")
            if retry_after:
                raise RateLimited(float(retry_after)) from err
            if remaining == "0" and reset:
                raise RateLimited(max(1.0, float(reset) - time.time())) from err
        detail = err.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{method} {path} -> {err.code}: {detail}") from err


def load_members() -> list[Member]:
    members = []
    for file in sorted(TOKEN_DIR.iterdir()):
        if file.name.startswith(".") or not file.is_file():
            continue
        token = file.read_text().strip()
        if not token:
            log.warning("empty token file %s, skipping", file.name)
            continue
        members.append(Member(login=file.name, token=token))
    return members


def verify_members(members: list[Member]) -> list[Member]:
    good = []
    for m in members:
        try:
            actual = gh(m.token, "GET", "/user")["login"]
        except Exception as err:  # noqa: BLE001 - report and keep the loop alive
            log.error("token for %s rejected: %s", m.login, err)
            continue
        if actual.lower() != m.login.lower():
            log.error("token file %s belongs to %s, skipping", m.login, actual)
            continue
        good.append(m)
    return good


def pending_prs(me: Member, buddy: str) -> list[tuple[str, int]]:
    query = f"is:pr is:open org:{ORG} author:{buddy} review-requested:{me.login}"
    path = f"/search/issues?q={urllib.parse.quote(query)}&per_page={MAX_PER_SEARCH}"
    result = gh(me.token, "GET", path)
    found = []
    for item in result.get("items", []):
        repo = item["repository_url"].split("/repos/")[1]
        found.append((repo, item["number"]))
    return found


def skip_reason(me: Member, buddies: set[str], repo: str, number: int) -> str | None:
    pr = gh(me.token, "GET", f"/repos/{repo}/pulls/{number}")
    author = pr["user"]["login"]
    if pr["state"] != "open":
        return f"not open ({pr['state']})"
    if author not in buddies:
        return f"author {author} is not a buddy"
    if SKIP_DRAFTS and pr["draft"]:
        return "draft"
    reviews = gh(me.token, "GET", f"/repos/{repo}/pulls/{number}/reviews?per_page=100")
    mine = [r["state"] for r in reviews if r["user"]["login"] == me.login and r["state"] != "COMMENTED"]
    if mine and mine[-1] == "APPROVED":
        return "already approved"
    return None


def approve(me: Member, repo: str, number: int) -> None:
    if DRY_RUN:
        log.info("[dry-run] %s would approve %s#%s", me.login, repo, number)
        return
    gh(me.token, "POST", f"/repos/{repo}/pulls/{number}/reviews", {"event": "APPROVE", "body": REVIEW_BODY})
    log.info("APPROVED %s#%s as %s", repo, number, me.login)


def run_once(members: list[Member]) -> None:
    logins = {m.login for m in members} | EXTRA_BUDDIES
    for me in members:
        buddies = logins - {me.login}
        for buddy in sorted(buddies):
            try:
                prs = pending_prs(me, buddy)
            except RateLimited as rl:
                log.warning("%s: %s", me.login, rl)
                time.sleep(min(rl.wait_seconds, 300))
                continue
            except Exception as err:  # noqa: BLE001 - one bad search must not stop the others
                log.error("%s: search for %s failed: %s", me.login, buddy, err)
                continue
            for repo, number in prs:
                try:
                    reason = skip_reason(me, buddies, repo, number)
                    if reason:
                        log.debug("%s skips %s#%s: %s", me.login, repo, number, reason)
                        continue
                    approve(me, repo, number)
                except RateLimited as rl:
                    log.warning("%s: %s", me.login, rl)
                    time.sleep(min(rl.wait_seconds, 300))
                except Exception as err:  # noqa: BLE001 - keep going for the remaining PRs
                    log.error("%s: %s#%s failed: %s", me.login, repo, number, err)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    once = "--once" in sys.argv
    while True:
        members = verify_members(load_members()) if TOKEN_DIR.is_dir() else []
        if members and (len(members) >= 2 or EXTRA_BUDDIES):
            break
        if once:
            log.error("need at least two valid member tokens in %s, found %d", TOKEN_DIR, len(members))
            return 1
        log.warning("waiting for tokens: %d valid in %s (need 2, or 1 plus EXTRA_BUDDIES)", len(members), TOKEN_DIR)
        HEARTBEAT.touch()
        time.sleep(POLL_SECONDS)
    log.info("members: %s | org=%s poll=%ss dry_run=%s", ", ".join(m.login for m in members), ORG, POLL_SECONDS, DRY_RUN)
    while True:
        started = time.monotonic()
        run_once(members)
        HEARTBEAT.touch()
        if once:
            return 0
        time.sleep(max(5.0, POLL_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
