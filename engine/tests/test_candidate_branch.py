"""Candidate-branch writer: creates a clean branch, never touches the working tree."""

import subprocess
from pathlib import Path

from driftline.cognition.strategist import PARAMS_TEMPLATE, write_candidate_branch


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "engine" / "driftline" / "strategy").mkdir(parents=True)
    (root / "candidates").mkdir()
    params = root / "engine" / "driftline" / "strategy" / "params.py"
    params.write_text(PARAMS_TEMPLATE.format(lookback=63, top_n=3, target_gross=0.9, rebalance_days=7))
    (root / "README.md").write_text("x")
    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    git("init", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    return root


def test_creates_branch_with_only_params_and_report(tmp_path):
    root = make_repo(tmp_path)
    report = tmp_path / "report-2026-09-01.md"
    report.write_text("# report")

    params = dict(lookback=126, top_n=2, target_gross=0.8, rebalance_days=21)
    branch = write_candidate_branch(params, report, "2026-09-01", repo_root=root)
    assert branch == "candidate/2026-09-01"

    # branch exists and contains the new params
    show = subprocess.run(
        ["git", "show", f"{branch}:engine/driftline/strategy/params.py"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    assert "LOOKBACK = 126" in show.stdout and "REBALANCE_DAYS = 21" in show.stdout

    # diff vs main touches exactly params.py + the report
    diff = subprocess.run(["git", "diff", "--name-only", "main", branch],
                          cwd=root, capture_output=True, text=True, check=True)
    changed = set(diff.stdout.split())
    assert changed == {"engine/driftline/strategy/params.py", "candidates/report-2026-09-01.md"}

    # working tree untouched, still on main
    head = subprocess.run(["git", "branch", "--show-current"], cwd=root,
                          capture_output=True, text=True, check=True)
    assert head.stdout.strip() == "main"
    live = (root / "engine" / "driftline" / "strategy" / "params.py").read_text()
    assert "LOOKBACK = 63" in live


def test_existing_branch_not_recreated(tmp_path):
    root = make_repo(tmp_path)
    report = tmp_path / "r.md"
    report.write_text("# r")
    params = dict(lookback=126, top_n=2, target_gross=0.8, rebalance_days=21)
    assert write_candidate_branch(params, report, "2026-09-01", repo_root=root)
    assert write_candidate_branch(params, report, "2026-09-01", repo_root=root) is None
