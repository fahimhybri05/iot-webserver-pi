"""Git-based update mechanism behind /update - replaces the firmware's OTA
binary upload (no equivalent on a Pi running from a git checkout).

Repo URL / branch / auth are dashboard-configurable (config.load_update_config()/
save_update_config()) instead of hardcoded, so the same install can point at a
fork, a private repo, or just a different branch without editing files by hand.

Two auth methods, both applied ONLY to the single git subprocess call - never
persisted into .git/config, so a token can't leak by someone running
`git remote -v` or reading the repo's git metadata later:
  - token:   HTTPS PAT, sent as a Basic auth header via `-c http.extraheader`
             (ephemeral - not the same as embedding it in the remote URL,
             which WOULD persist to .git/config).
  - ssh_key: private key content, written once to data/deploy_key (0600) and
             pointed at via GIT_SSH_COMMAND for that call only.
"""
import base64
import logging
import os
import stat
import subprocess

from app import config

log = logging.getLogger("updater")

DEPLOY_KEY_PATH = os.path.join(config.DATA_DIR, "deploy_key")


def has_ssh_key():
    return os.path.exists(DEPLOY_KEY_PATH)


def save_ssh_key(key_text):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    fd = os.open(DEPLOY_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(key_text if key_text.endswith("\n") else key_text + "\n")
    finally:
        os.chmod(DEPLOY_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600, belt-and-braces


def _git_auth(cfg):
    """Returns (extra_git_args, env) for the auth method in cfg. Neither
    persists anything into the repo's own .git/config."""
    env = os.environ.copy()
    method = cfg.get("auth_method", "none")

    if method == "token" and cfg.get("token"):
        basic = base64.b64encode(f"x-access-token:{cfg['token']}".encode()).decode()
        return ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"], env

    if method == "ssh_key" and has_ssh_key():
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {DEPLOY_KEY_PATH} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        )
        return [], env

    return [], env


def _run(args, env, cwd, timeout=60):
    log.info("git %s", " ".join(a for a in args if not a.lower().startswith("authorization")))
    return subprocess.run(
        ["git"] + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )


def run_git_update(repo_dir):
    """Fetch + reset the local branch to match origin/<branch> exactly
    (`checkout -B`, not a merge/rebase) - this is a deploy target, not a dev
    checkout, so any local commits on that branch are expected to be
    disposable and get overwritten. Git still refuses (non-zero exit,
    reported as failure) if uncommitted working-tree changes would be
    clobbered, e.g. someone hand-edited a file on the Pi. Returns
    (ok: bool, message: str)."""
    cfg = config.load_update_config()
    branch = cfg.get("branch") or "main"
    repo_url = cfg.get("repo_url", "").strip()
    extra_args, env = _git_auth(cfg)

    if repo_url:
        set_url = subprocess.run(
            ["git", "remote", "set-url", "origin", repo_url],
            cwd=repo_dir, capture_output=True, text=True, timeout=15,
        )
        if set_url.returncode != 0:
            return False, f"failed to set remote url: {set_url.stderr.strip()}"

    fetch = _run(extra_args + ["fetch", "origin", branch], env, repo_dir)
    if fetch.returncode != 0:
        return False, f"fetch failed: {fetch.stderr.strip()}"

    checkout = _run(["checkout", "-B", branch, f"origin/{branch}"], env, repo_dir)
    if checkout.returncode != 0:
        return False, f"checkout failed: {checkout.stderr.strip()}"

    return True, f"updated to origin/{branch}: {checkout.stdout.strip() or checkout.stderr.strip()}"
