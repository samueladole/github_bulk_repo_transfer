# GitHub Bulk Repository Transfer

Automates moving every repository from one GitHub account to another — with full rollback support, crash-safe logging, and the ability to resume interrupted runs.

---

## Features

- Transfers all owned repositories in one command
- Creates a **transfer log** after each successful move so you can always undo
- **Rollback** — return all repos to the original account at any time
- **Resume** — pick up from where you left off after a crash or interruption
- Validates both tokens before touching anything
- Handles pagination (works with accounts that have 100+ repos)
- Skips forked repos gracefully with clear error messages
- Ctrl+C safe — interrupting mid-run tells you exactly how to recover

---

## Requirements

- Python 3.10+
- `requests` library

```bash
pip install requests
```

---

## Setup — Personal Access Tokens

You need a **Classic PAT** for each account. Create them at:  
👉 https://github.com/settings/tokens → *Generate new token (classic)*

| Account | Required scopes |
|---|---|
| Source (original owner) | `repo` |
| Destination (new owner) | `repo` |

> **Never share or commit your tokens.** The script only holds them in memory and never writes them to the log file.

---

## Usage

### 1. Transfer all repos

```bash
python transfer_github_repos.py
```

You'll be prompted for both usernames and tokens. The script will:

1. Validate both tokens
2. List all repos it found with their visibility
3. Show you the rollback command before anything starts
4. Ask you to type `YES` to confirm
5. Transfer each repo one by one, logging every success immediately

A log file is created automatically, e.g. `transfer_log_20240625_143022.json`. **Keep this file** — it's what makes rollback possible.

---

### 2. Roll back (undo all transfers)

```bash
python transfer_github_repos.py --rollback transfer_log_20240625_143022.json
```

Reads the log file and transfers every successfully moved repo **back** to the original account. You'll be prompted for both tokens again and asked to confirm before anything happens.

If you run `--rollback` without a filename, you'll be prompted to enter the path:

```bash
python transfer_github_repos.py --rollback
```

---

### 3. Resume an interrupted transfer

```bash
python transfer_github_repos.py --resume transfer_log_20240625_143022.json
```

Skips any repos already recorded as transferred in the log and continues with the rest. Useful if the script crashed, you hit Ctrl+C, or you lost your network connection mid-run.

---

## The Transfer Log

Every run produces a JSON log file like this:

```json
{
  "created_at": "2024-06-25T14:30:22.000000+00:00",
  "source_user": "old-account",
  "dest_user": "new-account",
  "transferred": ["repo-a", "repo-b", "repo-c"],
  "failed": [
    { "name": "forked-repo", "reason": "HTTP 422: Forked repos cannot be transferred" }
  ],
  "rolled_back": []
}
```

The `transferred` list is updated **immediately** after each successful move, so even if the script dies mid-run, it accurately reflects what's been done. After a rollback, moved repos are shifted from `transferred` to `rolled_back`.

---

## What gets transferred (and what doesn't)

**Preserved by GitHub during transfer:**
- All commits, branches, and tags
- Issues and pull requests
- Collaborators and teams
- Stars and watchers
- Wikis

**Requires manual attention:**
- **Forks** — GitHub's API does not allow transferring forked repositories. These will show as failures in the summary; transfer them manually via *github.com → repo Settings → Transfer*.
- **GitHub Pages** — custom domain settings may need to be reconfigured.
- **Actions secrets** — repository secrets are not transferred.
- **Name conflicts** — if the destination account already has a repo with the same name, that transfer will fail.

---

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `HTTP 422: Forked repos cannot be transferred` | Repo is a fork | Transfer manually via GitHub web UI |
| `HTTP 422: name already exists` | Destination has a repo with the same name | Rename or delete the conflicting repo first |
| `HTTP 403: Must have admin rights` | Token missing `repo` scope | Regenerate the PAT with `repo` checked |
| `Token invalid` at startup | Wrong token or wrong username entered | Double-check the username matches the token owner |

---

## Rollback timing note

GitHub enforces a short cooldown between transfers of the same repository. If you transfer and immediately roll back, you may see a `422` error. **Wait 5–10 minutes** before running `--rollback` to avoid this.

---

## Example walkthrough

```
$ python transfer_github_repos.py

═══════════════════════════════════════════════════════
   GitHub Bulk Repository Transfer  (with rollback)
═══════════════════════════════════════════════════════

Source account username       : old-account
Source account PAT            : ghp_****
Destination account username  : new-account
Destination account PAT       : ghp_****

→  Verifying source token …
✔  Source token valid for @old-account
→  Verifying destination token …
✔  Destination token valid for @new-account

→  Fetching repos owned by @old-account …
→  Transfer log: transfer_log_20240625_143022.json  ← keep this for rollback!

Found 3 repo(s) total, 3 to transfer:

  • my-api  [private]
  • portfolio-site  [public]
  • scripts  [public]

⚠  Transfers from @old-account → @new-account can be ROLLED BACK by running:
   python transfer_github_repos.py --rollback transfer_log_20240625_143022.json

Type  YES  to proceed: YES

→  [1/3] Transferring 'my-api' …
✔    'my-api' — Transfer initiated
→  [2/3] Transferring 'portfolio-site' …
✔    'portfolio-site' — Transfer initiated
→  [3/3] Transferring 'scripts' …
✔    'scripts' — Transfer initiated

───────────────────────────────────────────────────────
Summary
───────────────────────────────────────────────────────
✔  Transferred : 3/3

Log saved: transfer_log_20240625_143022.json
To undo everything: python transfer_github_repos.py --rollback transfer_log_20240625_143022.json
```

---

## License

MIT