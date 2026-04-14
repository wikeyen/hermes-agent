from pathlib import Path
from types import SimpleNamespace

from hermes_cli.main import _sync_fork_with_rebase, _try_push_branch


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_try_push_branch_can_use_force_with_lease(monkeypatch):
    calls = []

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        calls.append(cmd)
        return _Completed(returncode=0)

    monkeypatch.setattr("hermes_cli.main.subprocess.run", fake_run)

    ok, output = _try_push_branch(["git"], Path("."), "origin", "main", force_with_lease=True)

    assert ok is True
    assert output == ""
    assert calls == [["git", "push", "--force-with-lease", "origin", "main"]]


def test_sync_fork_with_rebase_force_pushes_rebased_main(monkeypatch):
    monkeypatch.setattr("hermes_cli.main._has_upstream_remote", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("hermes_cli.main._fetch_remote", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("hermes_cli.main._branch_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("hermes_cli.main._ensure_local_branch_tracks_remote", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("hermes_cli.main._count_commits_between", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr("hermes_cli.main._git_rebase", lambda *_args, **_kwargs: (True, ""))

    push_calls = []

    def fake_push(git_cmd, cwd, remote, branch, force_with_lease=False):
        push_calls.append(
            {
                "git_cmd": git_cmd,
                "cwd": cwd,
                "remote": remote,
                "branch": branch,
                "force_with_lease": force_with_lease,
            }
        )
        return True, ""

    monkeypatch.setattr("hermes_cli.main._try_push_branch", fake_push)

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=None):
        return _Completed(returncode=0)

    monkeypatch.setattr("hermes_cli.main.subprocess.run", fake_run)

    ok = _sync_fork_with_rebase(["git"], Path("."), branch="main")

    assert ok is True
    assert push_calls == [
        {
            "git_cmd": ["git"],
            "cwd": Path("."),
            "remote": "origin",
            "branch": "main",
            "force_with_lease": True,
        }
    ]
