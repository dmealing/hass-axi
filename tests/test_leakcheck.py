"""The public-repository guard.

The scanner is the control that keeps installation-specific data out of this
repository, so it is tested adversarially: every rule against the shape it
claims, every rule against content that must NOT trip it, and the evasions that
a line-by-line scanner misses.

Nothing dirty is ever committed. Address shapes are assembled from fragments at
run time (the condensed pass deliberately does not join digits, so a fragmented
address is invisible to it), and JWTs are built by actually base64-encoding a
payload, so no `eyJ` literal exists in this file at all.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import leakcheck

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN = leakcheck.synthetic_jwt()
HEAD, PAYLOAD, SIGNATURE = TOKEN.split(".")


#: One realistic leak per rule. The name is the rule that must fire.
DIRTY = {
    "jwt": f"token: {TOKEN}",
    "bearer": "header " + "Bearer " + "aa11bb22cc33dd44ee55",
    "private-ip": "host " + "192." + "168.1.10",
    "cgnat-ip": "peer " + "100." + "101.102.103",
    "link-local-ip": "addr " + "169." + "254.10.20",
    "private-ipv6": "addr " + "fd12" + ":3456:789a::1",
    "remote-ui-host": "url https://" + "a1b2c3d4e5f6a7b8" + ".ui.nabu.casa",
    "lan-hostname": "url http://" + "homeassistant" + ".local:8123",
    "coordinates": "latitude: " + "51.5" + "0736",
    "mac-address": "mac " + "a4:c1" + ":38:9f:2b:7e",
    "home-path": "path " + "/ho" + "me/" + "someone" + "/notes",
    "personal-email": "mail " + "noreply" + "@" + "realcompany.co.uk",
}

#: Content this project legitimately contains, or that reads like a leak but is
#: not. A guard that fires on these gets ignored, which is its own failure mode.
CLEAN = [
    "host 127.0.0.1:8123",
    "bind 0.0.0.0 for the test double",
    "host 8.8.8.8",
    "host 172.15.0.1",
    "host 172.32.0.1",
    "host 100.63.255.1",
    "host 100.128.0.1",
    "url https://homeassistant.example.com",
    "entity light.example_lamp in area Example Room",
    "mail you@example.com",
    "mail noreply@anything.example",
    "curl -H 'Authorization: Bearer <token>'",
    'curl -H "Authorization: Bearer $HA_TOKEN"',
    "a bearer of good news arrives",
    "relative path src/hass_axi/cli.py",
    "version 2026.1.0 released",
    "python 3.9, 3.10, 3.12 supported",
    "line-length = 100",
    "timeout 30.5 seconds",
    "coverage rose from 51.5 to 90.2 percent",
    "the git empty tree is 4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "ff02::1 is the all-nodes multicast group",
    "fdfd is hex, not an address",
    "settings.local.json is ignored",
    "Path.home() resolves the home directory",
    "from .commands.home import DESCRIPTION",
    "a timestamp like 12:34:56 is not a MAC",
]


def rule_names(findings):
    return sorted({finding.rule.name for finding in findings})


@pytest.mark.parametrize(("rule", "content"), sorted(DIRTY.items()))
def test_every_rule_detects_its_shape(rule, content):
    assert rule in rule_names(leakcheck.scan_text("f.txt", content + "\n"))


def test_every_declared_rule_is_covered_by_this_suite():
    """A rule added without a test would otherwise look proven."""
    assert sorted(DIRTY) == sorted(rule.name for rule in leakcheck.RULES)


@pytest.mark.parametrize("content", CLEAN)
def test_legitimate_content_does_not_trip_the_scanner(content):
    assert leakcheck.scan_text("f.txt", content + "\n") == []


# --------------------------------------------------------------- evasions


def test_a_token_split_across_lines_is_caught():
    """A line pass cannot see this; the condensed pass is why it exists."""
    source = f'TOKEN = (\n    "{HEAD}."\n    "{PAYLOAD}."\n    "{SIGNATURE}"\n)\n'
    findings = leakcheck.scan_text("f.py", source)
    assert "jwt" in rule_names(findings)
    assert any(finding.pass_name == "joined" for finding in findings)


def test_a_token_joined_by_concatenation_is_caught():
    source = f'T = "{HEAD}" + "." + "{PAYLOAD}" + "." + "{SIGNATURE}"\n'
    assert "jwt" in rule_names(leakcheck.scan_text("f.py", source))


def test_a_token_continued_over_a_backslash_is_caught():
    source = f"token: {HEAD}.{PAYLOAD}.\\\n{SIGNATURE}\n"
    assert "jwt" in rule_names(leakcheck.scan_text("f.txt", source))


def test_a_percent_encoded_token_is_caught():
    source = f"u={HEAD}%2E{PAYLOAD}%2E{SIGNATURE}\n"
    assert "jwt" in rule_names(leakcheck.scan_text("f.txt", source))


def test_a_finding_is_reported_once_even_when_both_passes_see_it():
    findings = leakcheck.scan_text("f.txt", f"token: {TOKEN}\n")
    assert len(findings) == 1


def test_a_leak_twice_on_one_line_is_still_one_finding():
    doubled = f'repo = "{HOME}/a" and "{HOME}/b"\n'
    assert rule_names(leakcheck.scan_text("f.txt", doubled)) == ["home-path"]


# ----------------------------------------------------------- allow marker


def test_the_allow_marker_exempts_only_the_rule_it_names():
    dirty = "host " + "192." + "168.1.10" + " and " + "100." + "101.102.103"
    assert rule_names(leakcheck.scan_text("f.txt", dirty + "\n")) == ["cgnat-ip", "private-ip"]

    scoped = f"{dirty}  # {leakcheck.ALLOW_PREFIX}private-ip\n"
    assert rule_names(leakcheck.scan_text("f.txt", scoped)) == ["cgnat-ip"]


def test_the_allow_marker_accepts_several_named_rules():
    dirty = "host " + "192." + "168.1.10" + " and " + "100." + "101.102.103"
    scoped = f"{dirty}  # {leakcheck.ALLOW_PREFIX}private-ip,cgnat-ip\n"
    assert leakcheck.scan_text("f.txt", scoped) == []


def test_an_unscoped_marker_exempts_nothing():
    """A blanket marker would switch off rules nobody was thinking about."""
    dirty = "host " + "192." + "168.1.10" + "  # leakcheck: allow\n"
    assert rule_names(leakcheck.scan_text("f.txt", dirty)) == ["private-ip"]


def test_the_allow_marker_also_applies_to_the_condensed_pass():
    source = f"token: {TOKEN}  # {leakcheck.ALLOW_PREFIX}jwt\n"
    assert leakcheck.scan_text("f.txt", source) == []


# -------------------------------------------------------- path allowances
#
# The marker is a comment, and a file kept byte-for-byte cannot gain one.
# PATH_ALLOWANCES is how such a file is exempted instead, and these tests hold
# it to the same scope the marker has.


LOST_MESSAGE_PATH = "tests/fixtures/commit-messages/sibling-41bcb73.txt"
ALLOWED_PATH = LOST_MESSAGE_PATH
LUCKY_MESSAGE_PATH = "tests/fixtures/commit-messages/46c25f9.txt"
HIJACKED_MESSAGE_PATH = "tests/fixtures/commit-messages/sibling-b1f9bb18.txt"
TRAILER_ADDRESS = "noreply" + "@" + "anthropic" + ".com"
TWO_SHAPES = "mail " + TRAILER_ADDRESS + " and " + "192." + "168.1.10"


def test_a_path_allowance_exempts_only_the_rule_it_names():
    assert rule_names(leakcheck.scan_text(ALLOWED_PATH, TWO_SHAPES + "\n")) == ["private-ip"]


def test_a_path_allowance_exempts_no_other_file():
    findings = leakcheck.scan_text("src/hass_axi/output.py", TWO_SHAPES + "\n")
    assert rule_names(findings) == ["personal-email", "private-ip"]


def test_an_allowance_matches_only_the_exact_path_it_names():
    """A path that merely ends with an allowed one -- a shadowing directory, a
    suffixed twin, a scan rooted elsewhere -- is a different file, and exempting
    it would grant the entry every directory it is ever copied into."""
    assert leakcheck.path_allowances(ALLOWED_PATH) == frozenset({"personal-email"})
    assert leakcheck.path_allowances(f"attic/{ALLOWED_PATH}") == frozenset()
    assert leakcheck.path_allowances(f"/scan/root/{ALLOWED_PATH}") == frozenset()
    assert leakcheck.path_allowances(f"{ALLOWED_PATH}.bak") == frozenset()


#: The exact shapes each exempted file still trips with the allowances switched
#: off, so a refreshed fixture that changes one -- or grows a second -- fails the
#: suite instead of quietly widening what the entry covers. Assembled from
#: fragments like DIRTY, so this file stays clean under its own scanner.
EXPECTED_SHAPES = {
    LOST_MESSAGE_PATH: frozenset({TRAILER_ADDRESS}),
    LUCKY_MESSAGE_PATH: frozenset({TRAILER_ADDRESS}),
    HIJACKED_MESSAGE_PATH: frozenset({TRAILER_ADDRESS}),
}


def test_every_expected_shape_entry_names_a_live_allowance():
    assert sorted(EXPECTED_SHAPES) == sorted(leakcheck.PATH_ALLOWANCES)


@pytest.mark.parametrize("path", sorted(leakcheck.PATH_ALLOWANCES))
def test_every_path_allowance_is_still_earning_its_place(path, monkeypatch):
    """Stale is as bad as blanket: an exemption outlives what it was granted for.

    Scanning the real file with allowances switched off must fire exactly the
    rules the entry names -- no fewer, or the entry is dead and should go; no
    more, or it is covering something nobody agreed to -- and exactly the shapes
    recorded beside it, so a vendored refresh cannot change what is exempted
    without the change being visible here.
    """
    allowed = leakcheck.PATH_ALLOWANCES[path]
    assert allowed <= set(leakcheck.RULES_BY_NAME), "allowance names an undeclared rule"
    expected = EXPECTED_SHAPES[path]
    target = REPO_ROOT / path
    assert target.is_file(), "allowance names a file that is no longer here"

    monkeypatch.setattr(leakcheck, "PATH_ALLOWANCES", {})
    findings = leakcheck.scan_text(path, target.read_text(encoding="utf-8"))
    assert rule_names(findings) == sorted(allowed)
    assert {finding.matched for finding in findings} == expected


# ------------------------------------------------------------------ email


def test_an_allowlisted_local_part_no_longer_bypasses_the_domain_check():
    """`noreply@` on a real domain still identifies a real organisation."""
    dirty = "mail " + "noreply" + "@" + "realcompany.co.uk"
    assert rule_names(leakcheck.scan_text("f.txt", dirty + "\n")) == ["personal-email"]


# ------------------------------------------------------------- interfaces


def test_scanning_a_dirty_tree_exits_non_zero(tmp_path, capsys):
    fixture = leakcheck.dirty_fixture()
    for name, content in fixture.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    exit_code = leakcheck.main(["--root", str(tmp_path), *sorted(fixture)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "help:" in out


def test_the_self_test_confirms_every_rule_still_fires(capsys):
    assert leakcheck.main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "every rule fired" in out
    for rule in leakcheck.RULES:
        assert rule.name in out


def test_the_rules_listing_names_every_rule(capsys):
    assert leakcheck.main(["--rules"]) == 0
    out = capsys.readouterr().out
    for rule in leakcheck.RULES:
        assert rule.name in out


def test_this_repository_is_clean(capsys):
    assert leakcheck.main(["--root", str(REPO_ROOT)]) == 0
    assert "0 findings" in capsys.readouterr().out


def test_binary_files_are_skipped(tmp_path):
    dirty = ("192." + "168.1.1").encode()
    (tmp_path / "image.png").write_bytes(b"\x89PNG" + dirty)
    assert leakcheck.scan_paths(["image.png"], root=tmp_path) == []


def test_a_commit_message_is_scanned(tmp_path, capsys):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("fix: tested against " + "192." + "168.1.10" + "\n", encoding="utf-8")
    assert leakcheck.main(["--commit-msg", str(message)]) == 1
    assert "private-ip" in capsys.readouterr().out


def test_commit_message_comment_lines_are_ignored(tmp_path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("fix: something\n# host " + "192." + "168.1.10" + "\n", encoding="utf-8")
    assert leakcheck.main(["--commit-msg", str(message)]) == 0


# ------------------------------------------------------------------ hooks


def _hook_repo(tmp_path, *hooks):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".githooks").mkdir()
    # Both scripts, because `commit-msg` runs both: a repository missing one
    # would block every commit under `set -e` and look like the scanner firing.
    for script in ("leakcheck.py", "commitcheck.py"):
        (repo / "scripts" / script).write_text(
            (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8"), encoding="utf-8"
        )
    for name in hooks:
        target = repo / ".githooks" / name
        target.write_text(
            (REPO_ROOT / ".githooks" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
        target.chmod(0o755)
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "you@example.com"),
        ("config", "user.name", "Test"),
        ("config", "core.hooksPath", ".githooks"),
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return repo


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=check)


def test_the_pre_commit_hook_blocks_a_dirty_commit(tmp_path):
    repo = _hook_repo(tmp_path, "pre-commit")
    (repo / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-m", "clean").returncode == 0

    (repo / "dirty.txt").write_text("host " + "192." + "168.1.10" + "\n", encoding="utf-8")
    _git(repo, "add", "dirty.txt")
    blocked = _git(repo, "commit", "-m", "dirty", check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr


def test_the_pre_commit_hook_scans_the_very_first_commit(tmp_path):
    """A repository with no HEAD yet has nothing to diff against."""
    repo = _hook_repo(tmp_path, "pre-commit")
    (repo / "first.txt").write_text("host " + "10." + "1.2.3" + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    blocked = _git(repo, "commit", "-m", "first", check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr


def test_the_commit_msg_hook_blocks_a_dirty_message(tmp_path):
    """File content and the commit message are separate channels."""
    repo = _hook_repo(tmp_path, "pre-commit", "commit-msg")
    (repo / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    _git(repo, "add", "-A")
    dirty = "test: tested on " + "192." + "168.1.10"
    blocked = _git(repo, "commit", "-m", dirty, check=False)
    assert blocked.returncode != 0
    assert b"private-ip" in blocked.stdout + blocked.stderr

    # Conventional, because the same hook now also refuses a message
    # release-please cannot parse. See tests/test_commit_message.py.
    assert _git(repo, "commit", "-m", "test: tested on a local instance").returncode == 0


def test_identity_trailers_do_not_block_a_commit(tmp_path):
    """Git records author and committer addresses anyway; blocking these
    would stop ordinary attribution without preventing any leak."""
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "fix: something\n\nCo-Authored-By: Someone <someone@example.org>\n"
        "Signed-off-by: Other <" + "other" + "@" + "realcompany.co.uk" + ">\n",
        encoding="utf-8",
    )
    assert leakcheck.main(["--commit-msg", str(message)]) == 0


def test_an_address_in_the_message_body_is_still_caught(tmp_path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "fix: reported by " + "firstname.lastname" + "@" + "realcompany.co.uk" + "\n",
        encoding="utf-8",
    )
    assert leakcheck.main(["--commit-msg", str(message)]) == 1


def test_a_trailer_is_only_exempt_from_the_email_rule(tmp_path):
    """The exemption is about attribution, not a way to smuggle anything else."""
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "fix: x\n\nCo-Authored-By: host " + "192." + "168.1.10" + "\n", encoding="utf-8"
    )
    assert leakcheck.main(["--commit-msg", str(message)]) == 1


# ------------------------------------------------------- the pull request
#
# The third surface. A title and a body are published the moment they are
# written, are in no checkout and pass under no hook, so neither the tracked-file
# scan nor `--commit-msg` has ever seen one. The pipeline's document step writes
# into the body -- it pastes captured pytest output, and a pytest header carries
# a `rootdir:` line holding an absolute path. That has published a home directory
# twice, on two repositories, with every check green both times.
#
# Nothing dirty is written here either: the shapes are assembled from fragments,
# exactly as DIRTY above is.


HOME = "/ho" + "me/" + "someone"

#: Today's case, rebuilt: pytest output pasted into an evidence block.
LEAKY_BODY = (
    "## Evidence\n\n"
    "<details>\n<summary>pytest -q</summary>\n\n"
    "```\n"
    "platform linux -- Python 3.12.0, pytest-8.0.0, pluggy-1.5.0\n"
    f"rootdir: {HOME}/checkout\n"
    "collected 590 items\n"
    "```\n\n</details>\n"
)
LEAKY_TITLE = "fix(ci): run the suite from " + HOME + "/checkout"

#: The third arrival, rebuilt the same way: not pytest output this time but a
#: driver script pasted as evidence, with the same home directory on two of its
#: lines -- one leak, two places to fix.
DRIVER_BODY = f'## Evidence\n\nREPO = "{HOME}/worktrees/run"\nOUT = "{HOME}/evidence/run/t.txt"\n'

#: An ordinary pull request: prose, a code fence, repository-relative paths, a
#: documentation host, an attribution trailer. A guard that fires on this is one
#: people learn to route around, which is the failure mode that ends guards.
CLEAN_TITLE = "fix(toon): keep decimal form inside the canonical range"
CLEAN_BODY = (
    "## Intent\n\n"
    "`src/hass_axi/toon.py` formats through `Decimal(repr(value))` inside the range,\n"
    "and `tests/test_toon_conformance.py` covers it.\n\n"
    "```\n"
    "rootdir: /github/workspace\n"
    "collected 590 items\n"
    "```\n\n"
    "Checked against https://homeassistant.example.com with `light.example_lamp`\n"
    "in area Example Room, on 127.0.0.1:8123.\n\n"
    "Co-authored-by: Someone <someone@example.org>\n"
)


class _Transport:
    """A stand-in for the GitHub reader, so no test needs a network or a token."""

    def __init__(self, *, token="t0ken", slug="example/repo", data=None, error=None):
        self._token = token
        self._slug = slug
        self._data = {"title": "", "body": ""} if data is None else data
        self._error = error

    def github_token(self):
        return self._token

    def repo_slug(self, root="."):
        return self._slug

    def pull_request(self, number, *, slug, token, api_url=None):
        if self._error is not None:
            raise self._error
        return self._data


def _transport(monkeypatch, **kwargs):
    transport = _Transport(**kwargs)
    monkeypatch.setattr(leakcheck, "_github_transport", lambda: transport)
    return transport


def test_the_pull_request_body_that_shipped_today_is_caught():
    """A pytest `rootdir:` header inside an evidence block, which is how the
    pipeline's own document step publishes an absolute path."""
    findings = leakcheck.scan_pull_request((("title", "fix(ci): a subject"), ("body", LEAKY_BODY)))
    assert rule_names(findings) == ["home-path"]
    (finding,) = findings
    assert finding.path == "pull request body"
    assert LEAKY_BODY.splitlines()[finding.line_number - 1].startswith("rootdir:")
    assert finding.column is not None


def test_the_same_leak_on_two_lines_is_reported_on_both():
    """A driver script pasted as evidence carried the same home directory on two
    of its lines, and the report named only the first: fixing that line made the
    next run fail on the second, a finding the first run had already seen and
    stayed silent about. The pull request report prints no excerpt, so a line
    number is the whole locator -- every line the value sits on must be named."""
    findings = leakcheck.scan_pull_request((("title", "fix: a subject"), ("body", DRIVER_BODY)))
    assert [finding.rule.name for finding in findings] == ["home-path", "home-path"]
    assert [finding.line_number for finding in findings] == [3, 4]
    assert all(finding.column is not None for finding in findings)


def test_a_leak_in_the_title_is_caught_too():
    """The title is published in every listing, every notification and every
    merge subject; it is as public as the body and is scanned as one."""
    findings = leakcheck.scan_pull_request(
        (("title", LEAKY_TITLE), ("body", "## Intent\n\nordinary prose.\n"))
    )
    assert rule_names(findings) == ["home-path"]
    assert findings[0].path == "pull request title"


def test_every_field_github_publishes_is_scanned():
    """Both fields, in one call, so a leak in either fails the check."""
    findings = leakcheck.scan_pull_request((("title", LEAKY_TITLE), ("body", LEAKY_BODY)))
    assert sorted(f.path for f in findings) == [
        f"pull request {field}" for field in sorted(leakcheck.PULL_REQUEST_FIELDS)
    ]


def test_an_ordinary_pull_request_is_left_alone():
    assert leakcheck.scan_pull_request((("title", CLEAN_TITLE), ("body", CLEAN_BODY))) == []


def test_the_check_that_already_read_this_body_reports_it_clean():
    """The hole, stated as a test rather than as a claim.

    Before this scan existed the body was not unread -- `commitcheck
    --pull-request` fetched it on every pull request, in the very same job. It
    read it for a different question (does release-please parse what a merge
    hands it) and answered that question correctly, which is why every check was
    green while an absolute path sat in the body. The rules were never the
    problem; the reach was.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import commitcheck

    problems, faults = commitcheck.check_pull_request(
        "fix(ci): a subject", 12, LEAKY_BODY, engine="python"
    )
    assert (problems, faults) == ([], [])
    assert (
        leakcheck.scan_pull_request((("title", "fix(ci): a subject"), ("body", LEAKY_BODY))) != []
    )


def test_a_pull_request_cannot_exempt_itself_with_a_marker():
    """A file's `allow=` marker is committed, diffed and reviewed. The same text
    in a body is an off-switch anyone can add after every check has run, on the
    one artefact whose editability is why this surface needs a guard."""
    marked = f"rootdir: {HOME}/checkout  {leakcheck.ALLOW_PREFIX}home-path\n"
    assert rule_names(leakcheck.scan_pull_request((("title", "t"), ("body", marked)))) == [
        "home-path"
    ]
    # The same line in a file still exempts itself, so this is a policy about the
    # surface and not a change to the marker.
    assert leakcheck.scan_text("f.txt", marked) == []


def test_an_attribution_trailer_does_not_fail_a_pull_request():
    """GitHub's squash box offers the body as the commit message, so a trailer
    is as routine there as in one -- and is exempt from the address rule only."""
    trailer = "Co-authored-by: Someone <" + "someone" + "@" + "realcompany.co.uk" + ">\n"
    assert leakcheck.scan_pull_request((("title", "fix: x"), ("body", trailer))) == []
    smuggled = "Co-authored-by: host " + "192." + "168.1.10" + "\n"
    assert rule_names(leakcheck.scan_pull_request((("title", "fix: x"), ("body", smuggled)))) == [
        "private-ip"
    ]


def test_the_report_says_where_without_republishing_it(monkeypatch, capsys):
    """A pull request check runs on a public log. Printing the excerpt would
    republish the leak to a wider audience than the pull request page itself."""
    _transport(monkeypatch, data={"title": LEAKY_TITLE, "body": LEAKY_BODY})
    assert leakcheck.main(["--pull-request", "7"]) == 1
    out = capsys.readouterr().out
    assert HOME not in out
    assert "checkout" not in out
    assert "home-path" in out
    assert "field,line,offset,rule,pass" in out
    assert "title,1," in out and "body,8," in out
    assert "example/repo#7" in out


def test_the_report_names_every_line_a_repeated_leak_is_on(monkeypatch, capsys):
    """The report is the public CI log the operator reads to fix the body, and
    it never prints the matched text: the line numbers it lists are the only
    places to look. A second occurrence withheld from it is a second red run
    nobody could have seen coming."""
    _transport(monkeypatch, data={"title": "fix: a subject", "body": DRIVER_BODY})
    assert leakcheck.main(["--pull-request", "7"]) == 1
    out = capsys.readouterr().out
    assert "body,3," in out and "body,4," in out
    assert HOME not in out


def test_a_clean_pull_request_exits_zero(monkeypatch, capsys):
    _transport(monkeypatch, data={"title": CLEAN_TITLE, "body": CLEAN_BODY})
    assert leakcheck.main(["--pull-request", "7"]) == 0
    assert "0 findings" in capsys.readouterr().out


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token": None},
        {"slug": None},
        {"error": RuntimeError("503 Service Unavailable")},
        {"data": "not a pull request"},
    ],
    ids=["no-token", "no-repository", "github-unavailable", "unreadable-answer"],
)
def test_a_pull_request_that_cannot_be_read_fails_the_check(monkeypatch, capsys, kwargs):
    """Fail closed. Reporting `0 findings` for something it never saw would turn
    an unknown into an assurance, which is worse than not running at all."""
    _transport(monkeypatch, **kwargs)
    assert leakcheck.main(["--pull-request", "7"]) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "cannot read the pull request" in out
    assert "Refusing to report a verdict it cannot support." in out


def test_a_broken_transport_fails_the_check(monkeypatch, capsys):
    def explode():
        raise leakcheck.PullRequestUnavailable("cannot load the GitHub reader")

    monkeypatch.setattr(leakcheck, "_github_transport", explode)
    assert leakcheck.main(["--pull-request", "7"]) == 1
    assert "cannot read the pull request" in capsys.readouterr().out


def test_a_scanner_with_no_rules_refuses_rather_than_passing(monkeypatch, capsys):
    """The other half of failing closed: an empty rule set finds nothing, and
    `0 findings` from a scanner that loaded nothing reads exactly like success."""
    _transport(monkeypatch, data={"title": LEAKY_TITLE, "body": LEAKY_BODY})
    monkeypatch.setattr(leakcheck, "RULES", [])
    assert leakcheck.main(["--pull-request", "7"]) == 1
    assert "no rules loaded" in capsys.readouterr().out


def test_the_self_test_covers_the_pull_request_surface(monkeypatch, capsys):
    """`--demo` runs in CI before the real scan. A scanner that stopped reaching
    the pull request must fail there rather than reporting nothing to report."""
    assert leakcheck.run_demo() == 0
    assert "pull request" in capsys.readouterr().out
    monkeypatch.setattr(leakcheck, "scan_pull_request", lambda fields: [])
    assert leakcheck.run_demo() == 1
    assert "missed a field" in capsys.readouterr().out


def test_the_demo_fixtures_are_the_shapes_they_claim():
    assert rule_names(leakcheck.scan_pull_request(leakcheck.dirty_pull_request())) == ["home-path"]
    assert leakcheck.scan_pull_request(leakcheck.clean_pull_request()) == []


def test_the_self_test_output_passes_the_pull_request_scan(capsys):
    """The demo's output is published twice over: it runs in a public CI log as
    the first step of this check, and the pipeline pastes it into pull request
    bodies as evidence. Printing the sample values to prove the rules fire made
    that output leak-shaped by construction -- and the pasted evidence then
    failed `--pull-request` on this repository's own pull request, the guard
    catching its own self-test. The proof is the exit code and the per-rule
    lines; the values are the leak."""
    assert leakcheck.run_demo() == 0
    out = capsys.readouterr().out
    assert leakcheck.scan_pull_request((("title", "self-test evidence"), ("body", out))) == []


def test_an_empty_pull_request_argument_is_a_refusal_not_a_files_scan(monkeypatch, capsys):
    """A wrapper passing an empty variable is asking about no pull request.
    Falling through to the tracked-file scan would exit green having never
    fetched the artefact -- the false assurance this check exists to end."""
    _transport(monkeypatch, data=[])
    assert leakcheck.main(["--pull-request", ""]) == 1
    out = capsys.readouterr().out
    assert "cannot read the pull request" in out
    assert "tracked files" not in out


def test_an_offset_is_not_printed_when_it_would_not_locate_the_match(monkeypatch, capsys):
    """The offset must index the artefact as written or not be printed at all.

    A leak that surfaces only in the percent-decoded view of a line, or in the
    condensed pass, has no offset into anything the reader can open. Printing
    one anyway sends the one person who can fix it to a position where nothing
    is -- and the pass column still says which view caught it.
    """
    encoded = "cfg=" + urllib.parse.quote(HOME + "/checkout", safe="")
    _transport(monkeypatch, data={"title": "fix(ci): cfg", "body": encoded + "\n"})
    assert leakcheck.main(["--pull-request", "7"]) == 1
    out = capsys.readouterr().out
    assert "body,1,-,home-path,decoded" in out
    assert HOME not in out

    split = f"t: {HEAD}.\n{PAYLOAD}.{SIGNATURE}\n"
    _transport(monkeypatch, data={"title": "fix: x", "body": split})
    assert leakcheck.main(["--pull-request", "7"]) == 1
    out = capsys.readouterr().out
    assert "body,1,-,jwt,joined" in out
    assert TOKEN not in out and HEAD not in out


class _NoGitTransport(_Transport):
    def repo_slug(self, root="."):
        raise FileNotFoundError("No such file or directory: 'git'")


def test_a_transport_that_cannot_resolve_the_repository_refuses(monkeypatch, capsys):
    """`repo_slug` shells out to git with no guard of its own, so a machine
    without one must get the structured refusal, not a raw traceback."""
    monkeypatch.setattr(leakcheck, "_github_transport", lambda: _NoGitTransport())
    assert leakcheck.main(["--pull-request", "7"]) == 1
    out = capsys.readouterr().out
    assert "cannot read the pull request" in out
    assert "could not resolve a token or repository" in out


def test_the_hygiene_workflow_runs_this_where_it_can_block():
    """A guard that cannot fail the check is documentation.

    `edited` is the trigger that matters: the document step writes the evidence
    into the body *after* the pull request is opened, so a check that fired only
    on `opened` and `synchronize` would pass the empty original body and never
    see what replaced it.

    The workflow is parsed into what GitHub will actually run, not matched as
    text: a commented-out step line or prose that mentions a flag is invisible
    here, and a reformat of the trigger list is not a change of behaviour.
    """
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "hygiene.yml").read_text(encoding="utf-8")
    )
    trigger = workflow.get("on") or workflow.get(True) or {}
    types = (trigger.get("pull_request") or {}).get("types") or []
    if isinstance(types, str):
        types = [types]
    assert "edited" in types

    scanning = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            words = (step.get("run") or "").split()
            if any(word.endswith("leakcheck.py") for word in words) and "--pull-request" in words:
                assert "github.event.pull_request.number" in words
                grants = job["permissions"] if "permissions" in job else workflow.get("permissions")
                assert (grants or {}).get("pull-requests") == "read"
                scanning.append(step)
    assert scanning, "no step runs the pull request scan"
