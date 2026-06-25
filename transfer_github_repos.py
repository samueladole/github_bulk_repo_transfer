#!/usr/bin/env python3
"""
GitHub Bulk Repository Transfer Script  (with rollback & resume)
=================================================================
Transfers all repositories from one GitHub account to another,
recording every action so you can roll back at any time.

Requirements:
  pip install requests

.env file (optional)
--------------------
Create a  .env  file in the same directory to avoid typing credentials each run:

  SOURCE_GITHUB_USER=old-account
  SOURCE_GITHUB_TOKEN=ghp_xxxxxxxxxxxx
  DEST_GITHUB_USER=new-account
  DEST_GITHUB_TOKEN=ghp_xxxxxxxxxxxx

Any values not found in .env (or in environment variables) will be prompted for.
The .env file is never modified by this script. Add it to .gitignore if committing.

Modes
-----
  python transfer_github_repos.py                         # normal transfer
  python transfer_github_repos.py --rollback [LOG_FILE]  # undo all transfers
  python transfer_github_repos.py --resume   [LOG_FILE]  # skip already-done repos

LOG_FILE defaults to  transfer_log_<timestamp>.json  (auto-created on first run).
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ─── .env loader ──────────────────────────────────────────────────────────────

def load_dotenv(path: str = ".env") -> dict[str, str]:
    """
    Parse a .env file and merge values into os.environ (without overwriting
    variables that are already set in the environment).
    Returns a dict of the keys that were loaded from the file.
    """
    loaded: dict[str, str] = {}
    if not os.path.exists(path):
        return loaded

    with open(path) as f:
        for raw_line in f:
            line = raw_line.strip()
            # Skip blank lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")   # strip optional quotes
            if not key:
                continue
            loaded[key] = value
            # Only set if not already present in the real environment
            os.environ.setdefault(key, value)

    return loaded


def get_credential(env_key: str, prompt: str, secret: bool = False) -> str:
    """
    Return the value of env_key from the environment if set and non-empty,
    otherwise prompt the user.  Uses getpass for secret fields so the token
    isn't echoed to the terminal.
    """
    value = os.environ.get(env_key, "").strip()
    if value:
        return value
    if secret:
        import getpass
        return getpass.getpass(prompt).strip()
    return input(prompt).strip()


GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}✔  {msg}{RESET}")
def warn(msg): print(f"{YELLOW}⚠  {msg}{RESET}")
def err(msg):  print(f"{RED}✘  {msg}{RESET}")
def info(msg): print(f"{CYAN}→  {msg}{RESET}")
def head(msg): print(f"\n{BOLD}{CYAN}{msg}{RESET}")


# ─── State / log helpers ──────────────────────────────────────────────────────

def new_log(source_user: str, dest_user: str) -> dict:
    return {
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "source_user":  source_user,
        "dest_user":    dest_user,
        "source_token": "",   # never persisted
        "dest_token":   "",   # never persisted
        "transferred":  [],   # list of repo names successfully moved
        "failed":       [],   # list of {name, reason}
        "rolled_back":  [],   # list of repo names successfully rolled back
    }

def save_log(log: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(log, f, indent=2)

def load_log(path: str) -> dict:
    with open(path) as f:
        return json.load(f)

def log_transferred(log: dict, path: str, repo_name: str) -> None:
    if repo_name not in log["transferred"]:
        log["transferred"].append(repo_name)
    save_log(log, path)

def log_failed(log: dict, path: str, repo_name: str, reason: str) -> None:
    log["failed"].append({"name": repo_name, "reason": reason})
    save_log(log, path)

def log_rolled_back(log: dict, path: str, repo_name: str) -> None:
    if repo_name not in log["rolled_back"]:
        log["rolled_back"].append(repo_name)
    if repo_name in log["transferred"]:
        log["transferred"].remove(repo_name)
    save_log(log, path)


# ─── GitHub helpers ───────────────────────────────────────────────────────────

def gh_headers(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}


def verify_token(token: str, expected_user: str) -> bool:
    r = requests.get("https://api.github.com/user",
                     headers=gh_headers(token), timeout=15)
    if r.status_code != 200:
        return False
    return r.json().get("login", "").lower() == expected_user.lower()


def get_all_repos(username: str, token: str) -> list[dict]:
    repos, page = [], 1
    while True:
        r = requests.get(
            "https://api.github.com/user/repos",
            headers=gh_headers(token),
            params={"affiliation": "owner", "per_page": 100, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        repos.extend(repo for repo in batch
                     if repo["owner"]["login"].lower() == username.lower())
        page += 1
    return repos


def transfer_repo(repo_name: str, from_user: str, from_token: str,
                  to_user: str) -> tuple[bool, str]:
    url = f"https://api.github.com/repos/{from_user}/{repo_name}/transfer"
    r = requests.post(url, headers=gh_headers(from_token),
                      json={"new_owner": to_user}, timeout=30)
    if r.status_code in (200, 202):
        return True, "Transfer initiated"
    try:
        detail = r.json().get("message", r.text)
    except Exception:
        detail = r.text
    return False, f"HTTP {r.status_code}: {detail}"


# ─── Transfer mode ────────────────────────────────────────────────────────────

def run_transfer(resume_log_path: str | None) -> None:
    head("═" * 55)
    print("   GitHub Bulk Repository Transfer  (with rollback)")
    print("═" * 55)

    # Load .env if present
    loaded = load_dotenv()
    if loaded:
        found = [k for k in ("SOURCE_GITHUB_USER", "SOURCE_GITHUB_TOKEN",
                              "DEST_GITHUB_USER",   "DEST_GITHUB_TOKEN") if k in loaded]
        if found:
            info(f".env loaded — using: {', '.join(found)}")

    source_user  = get_credential("SOURCE_GITHUB_USER",  "Source account username       : ")
    source_token = get_credential("SOURCE_GITHUB_TOKEN", "Source account PAT            : ", secret=True)
    dest_user    = get_credential("DEST_GITHUB_USER",    "Destination account username  : ")
    dest_token   = get_credential("DEST_GITHUB_TOKEN",   "Destination account PAT       : ", secret=True)

    if not all([source_user, source_token, dest_user, dest_token]):
        err("One or more required credentials are missing."); sys.exit(1)

    print(f"\n  Source : @{source_user}")
    print(f"  Dest   : @{dest_user}\n")

    # Validate tokens
    info("Verifying source token …")
    if not verify_token(source_token, source_user):
        err(f"Source token invalid or not owned by '{source_user}'."); sys.exit(1)
    ok(f"Source token valid for @{source_user}")

    info("Verifying destination token …")
    if not verify_token(dest_token, dest_user):
        err(f"Destination token invalid or not owned by '{dest_user}'."); sys.exit(1)
    ok(f"Destination token valid for @{dest_user}\n")

    # Fetch repos
    info(f"Fetching repos owned by @{source_user} …")
    try:
        all_repos = get_all_repos(source_user, source_token)
    except requests.HTTPError as e:
        err(f"Failed to list repos: {e}"); sys.exit(1)

    if not all_repos:
        warn("No repos found on the source account."); sys.exit(0)

    # Resume support: skip already transferred repos
    already_done: set[str] = set()
    log_path: str

    if resume_log_path:
        log = load_log(resume_log_path)
        already_done = set(log["transferred"])
        log_path = resume_log_path
        warn(f"Resuming from {resume_log_path} — "
             f"skipping {len(already_done)} already-transferred repo(s).")
    else:
        log = new_log(source_user, dest_user)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = f"transfer_log_{ts}.json"
        save_log(log, log_path)
        info(f"Transfer log: {log_path}  ← keep this for rollback!")

    repos_to_transfer = [r for r in all_repos if r["name"] not in already_done]

    print(f"\nFound {len(all_repos)} repo(s) total, "
          f"{len(repos_to_transfer)} to transfer:\n")
    for r in repos_to_transfer:
        vis = "private" if r["private"] else "public"
        print(f"  • {r['name']}  [{vis}]")
    if already_done:
        print(f"\n  (skipping {len(already_done)} already done: "
              f"{', '.join(sorted(already_done))})")

    print(f"\n{YELLOW}Transfers from @{source_user} → @{dest_user} "
          f"can be ROLLED BACK by running:{RESET}")
    print(f"  python transfer_github_repos.py --rollback {log_path}\n")

    confirm = input("Type  YES  to proceed: ").strip()
    if confirm != "YES":
        warn("Aborted — nothing transferred."); sys.exit(0)

    # Store tokens in memory only (never written to log)
    log["source_token_hint"] = source_token[:4] + "…"
    log["dest_token_hint"]   = dest_token[:4]   + "…"

    print()
    succeeded, failed_list = [], []

    def _abort_cleanly(sig=None, frame=None):
        print(f"\n{YELLOW}Interrupted! Transferred so far: {len(succeeded)} repo(s).{RESET}")
        print(f"Run  python transfer_github_repos.py --rollback {log_path}  to undo.")
        sys.exit(130)

    import signal
    signal.signal(signal.SIGINT, _abort_cleanly)

    for i, repo in enumerate(repos_to_transfer, 1):
        name = repo["name"]
        info(f"[{i}/{len(repos_to_transfer)}] Transferring '{name}' …")

        success, message = transfer_repo(name, source_user, source_token, dest_user)

        if success:
            ok(f"  '{name}' — {message}")
            succeeded.append(name)
            log_transferred(log, log_path, name)
        else:
            err(f"  '{name}' — {message}")
            failed_list.append((name, message))
            log_failed(log, log_path, name, message)

        if i < len(repos_to_transfer):
            time.sleep(0.5)

    # Summary
    print(f"\n{CYAN}{'─'*55}")
    print("Summary")
    print(f"{'─'*55}{RESET}")
    ok(f"Transferred : {len(succeeded)}/{len(repos_to_transfer)}")

    if failed_list:
        err(f"Failed      : {len(failed_list)}/{len(repos_to_transfer)}")
        print(f"\n{RED}Failed repos:{RESET}")
        for name, reason in failed_list:
            print(f"  • {name}  —  {reason}")
        print("\nCommon reasons: repo is a fork · name clash on destination · "
              "token missing 'repo' scope")

    print(f"\n{GREEN}Log saved: {log_path}{RESET}")
    print(f"To undo everything: "
          f"python transfer_github_repos.py --rollback {log_path}")
    print()


# ─── Rollback mode ────────────────────────────────────────────────────────────

def run_rollback(log_path: str) -> None:
    head("═" * 55)
    print("   GitHub Repository Transfer  —  ROLLBACK")
    print("═" * 55)

    if not os.path.exists(log_path):
        err(f"Log file not found: {log_path}"); sys.exit(1)

    # Load .env credentials if present
    loaded = load_dotenv()
    if loaded:
        found = [k for k in ("SOURCE_GITHUB_TOKEN", "DEST_GITHUB_TOKEN") if k in loaded]
        if found:
            info(f".env loaded — using: {', '.join(found)}")

    log = load_log(log_path)
    source_user = log["source_user"]
    dest_user   = log["dest_user"]
    to_rollback = list(log["transferred"])   # repos currently on dest account

    if not to_rollback:
        warn("Nothing to roll back — 'transferred' list is empty.")
        if log.get("rolled_back"):
            ok(f"Already rolled back: {', '.join(log['rolled_back'])}")
        sys.exit(0)

    print(f"\nLog file  : {log_path}")
    print(f"Direction : @{dest_user}  →  @{source_user}  (reverse of original)")
    print(f"\nRepos to roll back ({len(to_rollback)}):")
    for name in to_rollback:
        print(f"  • {name}")

    print(f"\n{YELLOW}This will transfer the repos listed above BACK to @{source_user}.{RESET}")
    print("Tokens are read from .env if present, otherwise you'll be prompted.\n")

    # In rollback, dest is the CURRENT owner so we swap the env key meanings
    dest_token   = get_credential("DEST_GITHUB_TOKEN",   f"PAT for @{dest_user} (current owner)   : ", secret=True)
    source_token = get_credential("SOURCE_GITHUB_TOKEN", f"PAT for @{source_user} (original owner) : ", secret=True)
    print()

    info(f"Verifying @{dest_user} token …")
    if not verify_token(dest_token, dest_user):
        err("Token invalid."); sys.exit(1)
    ok(f"Token valid for @{dest_user}")

    info(f"Verifying @{source_user} token …")
    if not verify_token(source_token, source_user):
        err("Token invalid."); sys.exit(1)
    ok(f"Token valid for @{source_user}\n")

    confirm = input("Type  YES  to roll back: ").strip()
    if confirm != "YES":
        warn("Aborted."); sys.exit(0)

    print()
    rb_succeeded, rb_failed = [], []

    import signal
    def _abort(sig=None, frame=None):
        print(f"\n{YELLOW}Interrupted! Rolled back so far: {len(rb_succeeded)} repo(s).{RESET}")
        save_log(log, log_path)
        sys.exit(130)
    signal.signal(signal.SIGINT, _abort)

    for i, name in enumerate(to_rollback, 1):
        info(f"[{i}/{len(to_rollback)}] Rolling back '{name}' …")
        success, message = transfer_repo(name, dest_user, dest_token, source_user)

        if success:
            ok(f"  '{name}' — back to @{source_user}")
            rb_succeeded.append(name)
            log_rolled_back(log, log_path, name)
        else:
            err(f"  '{name}' — {message}")
            rb_failed.append((name, message))

        if i < len(to_rollback):
            time.sleep(0.5)

    # Summary
    print(f"\n{CYAN}{'─'*55}")
    print("Rollback Summary")
    print(f"{'─'*55}{RESET}")
    ok(f"Rolled back : {len(rb_succeeded)}/{len(to_rollback)}")

    if rb_failed:
        err(f"Failed      : {len(rb_failed)}/{len(to_rollback)}")
        print(f"\n{RED}Could not roll back:{RESET}")
        for name, reason in rb_failed:
            print(f"  • {name}  —  {reason}")
        print("\nYou may need to transfer these manually on github.com.")
    else:
        print(f"\n{GREEN}All repos successfully returned to @{source_user}.{RESET}")

    print(f"\nUpdated log: {log_path}\n")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Bulk-transfer GitHub repos with rollback support.")
    parser.add_argument("--rollback", metavar="LOG_FILE", nargs="?", const="PROMPT",
                        help="Roll back transfers recorded in LOG_FILE.")
    parser.add_argument("--resume", metavar="LOG_FILE", nargs="?", const="PROMPT",
                        help="Resume a transfer, skipping already-transferred repos.")
    args = parser.parse_args()

    def resolve_log(value: str | None) -> str:
        if value is None or value == "PROMPT":
            path = input("Path to transfer log file: ").strip()
            if not path:
                err("No log file provided."); sys.exit(1)
            return path
        return value

    if args.rollback:
        run_rollback(resolve_log(args.rollback))
    elif args.resume:
        run_transfer(resume_log_path=resolve_log(args.resume))
    else:
        run_transfer(resume_log_path=None)


if __name__ == "__main__":
    main()