from __future__ import annotations

import subprocess

from lingclaude.engine.git import (
    GitResult,
    git_blame,
    git_diff,
    git_log,
    git_status,
    is_git_repo,
)


class TestGitResult:
    def test_success_property(self) -> None:
        r = GitResult(
            exit_code=0,
            output="hello",
            error="",
            duration=0.1,
        )
        assert r.success is True

    def test_success_property_nonzero(self) -> None:
        r = GitResult(
            exit_code=1,
            output="",
            error="error",
            duration=0.1,
        )
        assert r.success is False


class TestIsGitRepo:
    def test_current_repo_is_git(self) -> None:
        assert is_git_repo(".") is True

    def test_non_git_directory(self, tmp_path) -> None:
        d = tmp_path / "nogit"
        d.mkdir()
        assert is_git_repo(str(d)) is False


class TestGitStatus:
    def test_status_in_git_repo(self) -> None:
        result = git_status(".")
        assert result.is_ok
        assert "files" in result.data
        assert "has_changes" in result.data
        assert isinstance(result.data["files"], list)

    def test_status_short_format(self) -> None:
        result = git_status(".", short=True)
        assert result.is_ok
        assert "raw" in result.data

    def test_status_with_path(self, tmp_path) -> None:
        # Create a temporary git repo
        d = tmp_path / "test_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        # Create a file and commit
        f = d / "test.txt"
        f.write_text("hello")
        subprocess.run(["git", "add", "test.txt"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(d), check=True, capture_output=True)

        result = git_status(str(d))
        assert result.is_ok
        assert result.data["has_changes"] is False

    def test_status_detects_changes(self, tmp_path) -> None:
        d = tmp_path / "changed_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "changed.txt"
        f.write_text("content")

        result = git_status(str(d))
        assert result.is_ok
        assert result.data["has_changes"] is True
        assert len(result.data["files"]) == 1


class TestGitDiff:
    def test_diff_no_changes(self, tmp_path) -> None:
        d = tmp_path / "clean_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "file.txt"
        f.write_text("content")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(d), check=True, capture_output=True)

        result = git_diff(str(d))
        assert result.is_ok
        assert result.data["has_changes"] is False

    def test_diff_with_changes(self, tmp_path) -> None:
        d = tmp_path / "diff_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "file.txt"
        f.write_text("line1")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(d), check=True, capture_output=True)

        f.write_text("line1\nline2")

        result = git_diff(str(d))
        assert result.is_ok
        assert result.data["has_changes"] is True
        assert "line2" in result.data["diff"]

    def test_diff_staged(self, tmp_path) -> None:
        d = tmp_path / "staged_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "file.txt"
        f.write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=str(d), check=True, capture_output=True)

        result = git_diff(str(d), staged=True)
        assert result.is_ok
        assert result.data["staged"] is True

    def test_diff_stat(self, tmp_path) -> None:
        d = tmp_path / "stat_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "file.txt"
        f.write_text("old")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(d), check=True, capture_output=True)

        f.write_text("new")

        result = git_diff(str(d), stat=True)
        assert result.is_ok
        assert "file.txt" in result.data["diff"] or result.data["has_changes"]

    def test_diff_with_target_path(self, tmp_path) -> None:
        d = tmp_path / "target_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f1 = d / "file1.txt"
        f1.write_text("content1")
        f2 = d / "file2.txt"
        f2.write_text("content2")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(d), check=True, capture_output=True)

        f1.write_text("modified")

        result = git_diff(str(d), target="file1.txt")
        assert result.is_ok


class TestGitLog:
    def test_log_default_count(self) -> None:
        result = git_log(".", count=10)
        assert result.is_ok
        assert "commits" in result.data
        assert "count" in result.data
        assert isinstance(result.data["commits"], list)

    def test_log_oneline_format(self) -> None:
        result = git_log(".", count=5, oneline=True)
        assert result.is_ok
        assert len(result.data["commits"]) <= 5
        for commit in result.data["commits"]:
            assert "hash" in commit
            assert "message" in commit
            assert len(commit["hash"]) == 7  # Short hash

    def test_log_with_count(self) -> None:
        result = git_log(".", count=3)
        assert result.is_ok
        assert len(result.data["commits"]) <= 3

    def test_log_with_follow(self, tmp_path) -> None:
        d = tmp_path / "follow_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "file.txt"
        f.write_text("content")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "first"], cwd=str(d), check=True, capture_output=True)

        f.write_text("updated")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "second"], cwd=str(d), check=True, capture_output=True)

        result = git_log(str(d), count=10, follow="file.txt")
        assert result.is_ok
        assert result.data["count"] == 2


class TestGitBlame:
    def test_blame_file(self, tmp_path) -> None:
        d = tmp_path / "blame_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "code.txt"
        f.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add code"], cwd=str(d), check=True, capture_output=True)

        result = git_blame(str(f), cwd=str(d))
        assert result.is_ok
        assert "lines" in result.data
        assert "commits" in result.data
        assert result.data["total_lines"] == 3
        assert len(result.data["lines"]) == 3

        for line_info in result.data["lines"]:
            assert "hash" in line_info
            assert "code" in line_info

    def test_blame_with_line_range(self, tmp_path) -> None:
        d = tmp_path / "range_repo"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(d), check=True, capture_output=True)

        f = d / "file.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        subprocess.run(["git", "add", "."], cwd=str(d), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=str(d), check=True, capture_output=True)

        result = git_blame(str(f), cwd=str(d), start_line=2, end_line=4)
        assert result.is_ok
        assert result.data["total_lines"] == 3

    def test_blame_file_not_found(self, tmp_path) -> None:
        result = git_blame("nonexistent.txt", cwd=str(tmp_path))
        assert result.is_error

    def test_blame_python_file(self) -> None:
        # Test with a real Python file in the repo
        result = git_blame("lingclaude/engine/git.py", cwd=".")
        assert result.is_ok
        assert "lines" in result.data
        assert "commits" in result.data
        assert result.data["total_lines"] > 0
