"""The guard that keeps an unreleasable commit message off `main`.

`scripts/commitcheck.py` answers one question -- would release-please read this
commit, or silently drop it? -- and the answer is only worth anything if it is
the same answer release-please's own parser gives. So this suite is built the
way `test_toon_conformance.py` is: the project's opinion is stated in its own
words *and* checked against a vendored copy of the upstream implementation.

Three things are pinned here, and they are not interchangeable:

* **The regression.** The real, byte-for-byte message of the commit that was
  lost, with the exact position the release workflow reported. A guard written
  from a description of a bug is a guard against the description. There are two:
  the message the sibling lost, and the sibling commit *after* it whose message
  was impeccable and was not the string release-please read at all -- the body of
  its pull request had replaced it. The second is the one that matters here,
  because the guard that shipped for the first passed it. It is the
  *sibling* project's commit, because that is where the loss happened -- and
  beside it sits this repository's own commit describing the same fix, which
  parsed. The two differ by where one phrase sits on its line. That is the whole
  case for this guard here: nothing has been lost yet, and the reason is luck.
* **The corpus.** Shapes that must be refused and, just as important, the rich
  bodies this fleet writes that must keep working. A validator that quietly
  started refusing prose would be the more expensive failure, because the answer
  to it is to stop writing the prose.
* **The agreement.** Every corpus entry through both engines, compared on the
  verdict *and* on the reported line, column and token. The Python transcription
  stands in for the real parser wherever `node` is absent, and a stand-in that
  disagreed would be worse than a skip.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import commitcheck

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "vendor" / "conventional-commits-parser"
CHECKSUMS = VENDOR_ROOT / "checksums.txt"
MESSAGES = Path(__file__).parent / "fixtures" / "commit-messages"

#: The sibling project's commit that release-please dropped, and the error it
#: printed while exiting 0. Both are transcribed from that workflow run, not
#: reconstructed. The commit is not in this repository's history; the message is
#: vendored because the shape is what has to be pinned, not the provenance.
LOST_SHA = "41bcb73e6b1797bee245bea2cd4797460b5cdbb5"
LOST_ERROR = "unexpected token '(' at 13:14, valid tokens [)]"

#: The sibling's *next* commit, whose message was fine and was not the one
#: release-please read: the body of its pull request replaced it, and the release
#: run reported this while both guard jobs were green. Transcribed from that run.
HIJACKED_SHA = "b1f9bb18ea7d6d9dffc1d44e17c90bd6fc0cd5d3"
HIJACKED_ERROR = "unexpected token ' ' at 1:6, valid tokens [(, !, :]"

#: This repository's own commit describing the same two TOON fixes. It parses,
#: and the only reason is that the phrase that broke the sibling sits one word
#: further along its line here.
LUCKY_SHA = "46c25f9"

#: Engines to run every shared assertion under. `node` is skipped rather than
#: failed where it is unavailable -- but CI installs it, so the agreement is
#: enforced on every push rather than only where somebody happens to have it.
ENGINES = ["python", "node"]

NODE = pytest.mark.skipif(not commitcheck.node_available(), reason="node is not available")


def engine_param(engine):
    return pytest.param(engine, marks=[NODE] if engine == "node" else [])


ENGINE_PARAMS = [engine_param(engine) for engine in ENGINES]


@pytest.fixture(scope="module")
def lost_message():
    return (MESSAGES / "sibling-41bcb73.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The regression: the message that was actually lost.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
def test_the_lost_commit_message_is_refused(lost_message, engine):
    problems = commitcheck.check(lost_message, engine=engine)
    assert problems, "the message that cost a release must not be accepted"
    assert len(problems) == 1
    assert str(problems[0].error) == LOST_ERROR


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
def test_the_lost_commit_message_is_located_where_the_workflow_said(lost_message, engine):
    problem = commitcheck.check(lost_message, engine=engine)[0]
    assert (problem.line, problem.column) == (13, 14)
    # Column 14 is the *second* parenthesis, the one the scope cannot contain.
    assert problem.source_line.startswith("`Decimal(repr(value))`")
    assert problem.source_line[problem.column - 1] == "("


def test_the_report_names_the_line_and_says_what_to_change(lost_message, capsys):
    problems = commitcheck.check(lost_message, engine="python")
    assert commitcheck.report(problems, "this commit message", stream=sys.stdout) == 1
    out = capsys.readouterr().out
    assert "line 13, column 14" in out
    assert "`Decimal(repr(value))`" in out
    # Advice a reader can act on without knowing the grammar: what to move, and
    # the fact that the prose itself is fine.
    assert "reflow" in out
    assert "fine mid-line" in out
    # And what the silence costs, since that is the part nobody sees.
    assert "still exit 0" in out


@pytest.fixture(scope="module")
def hijacked_message():
    """The sibling commit whose message was perfect and was not what was read."""
    return (MESSAGES / "sibling-b1f9bb18.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def hijacked_body():
    """The slice of the pull request body that replaced it.

    Transcribed from the live pull request through the REST API, not retyped:
    the bullet that named the marker, and the one after it. The paragraph the
    parser was handed begins at the word straight after the marker.
    """
    return (MESSAGES / "sibling-b1f9bb18-pull-request-body.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lucky_message():
    return (MESSAGES / "46c25f9.txt").read_text(encoding="utf-8")


def test_this_repositorys_own_message_about_the_same_fix_parses(lucky_message):
    """It does, and that is the point: it very nearly did not."""
    assert not commitcheck.check(lucky_message, engine="python")


def test_the_lucky_message_is_the_commit_that_is_actually_in_this_repository(lucky_message):
    """The near miss must stay the real message, not a retyped likeness.

    Skipped rather than failed where the object is unreachable: a shallow clone
    is a legitimate checkout, and this assertion is about provenance, not about
    whether the guard works.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%B", LUCKY_SHA],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("the commit is not reachable from this checkout")
    assert result.stdout == lucky_message


def test_the_two_messages_differ_only_in_where_the_phrase_sits(lost_message, lucky_message):
    """Both describe the same fix and both contain the same term.

    One begins a line with it and is unparseable; the other reaches it a few
    words in and is fine. Nothing about the prose, the punctuation or the
    vocabulary distinguishes them -- which is exactly why a person cannot be the
    control here.
    """
    term = "`Decimal(repr(value))`"
    assert term in lost_message
    assert term in lucky_message
    assert any(line.startswith(term) for line in lost_message.splitlines())
    assert not any(line.startswith(term) for line in lucky_message.splitlines())
    assert commitcheck.check(lost_message, engine="python")
    assert not commitcheck.check(lucky_message, engine="python")


def test_every_commit_in_this_repositorys_history_is_readable():
    """The claim the release audit makes, asserted rather than assumed.

    Skipped on a shallow clone, where the history is not there to check.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--first-parent", "-z", "--format=%H%n%B"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("the history is not reachable from this checkout")
    unreadable = []
    for record in result.stdout.split("\0"):
        if not record.strip():
            continue
        sha, _, body = record.partition("\n")
        if sha.strip() in commitcheck.KNOWN_UNPARSEABLE:
            continue
        if commitcheck.check(body, engine="python"):
            unreadable.append(sha.strip()[:12])
    assert not unreadable, f"release-please would drop: {unreadable}"


# ---------------------------------------------------------------------------
# The second regression: the message that was fine, and was not the one read.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
def test_the_hijacked_commit_message_parses_perfectly_on_its_own(hijacked_message, engine):
    """The false pass, stated first, because it is the whole defect.

    Nothing is wrong with this commit message. A guard that reads commit
    messages therefore passes it, reports success, and says nothing while
    release-please drops the commit and skips the release.
    """
    assert not commitcheck.check(hijacked_message, engine=engine)


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
def test_the_pull_request_body_is_what_release_please_actually_read(
    hijacked_message, hijacked_body, engine
):
    problems = commitcheck.check(hijacked_message, engine=engine, pull_request_body=hijacked_body)
    assert problems, "the body replaces the message, so the body is what must be judged"
    assert str(problems[0].error) == HIJACKED_ERROR
    assert problems[0].artefact == commitcheck.OVERRIDE_ARTEFACT


def test_column_six_is_the_space_after_a_five_letter_word(hijacked_message, hijacked_body):
    """Why the reported position looked impossible on a clean `fix(ci):` subject.

    Column 6 is nowhere near an error on the subject line the run printed. It is
    line 1 of a *different* text: the paragraph after the marker, whose first
    word is five characters long, followed by the space the grammar refuses
    because it wanted `(`, `!` or `:`.
    """
    problem = commitcheck.check(hijacked_message, engine="python", pull_request_body=hijacked_body)[
        0
    ]
    assert (problem.line, problem.column) == (1, 6)
    assert problem.source_line.split(" ")[0] == "block"
    assert len(problem.source_line.split(" ")[0]) == 5
    assert problem.source_line[problem.column - 1] == " "
    assert problem.error.valid == ["(", "!", ":"]


def test_the_report_says_which_artefact_and_what_to_do_about_it(
    hijacked_message, hijacked_body, capsys
):
    problems = commitcheck.check(hijacked_message, engine="python", pull_request_body=hijacked_body)
    assert commitcheck.report(problems, "this commit", stream=sys.stdout) == 1
    out = capsys.readouterr().out
    # Naming the artefact is the point: "fix your commit message" is useless
    # advice to somebody whose commit message is already correct.
    assert "pull request body" in out
    assert "NOT the commit message" in out
    assert "describe it instead" in out


# ---------------------------------------------------------------------------
# The transcription of release-please's own preprocessing.
# ---------------------------------------------------------------------------


def test_naming_the_marker_is_enough_to_trigger_it():
    """`String.split` has no idea it is reading prose. Neither does this."""
    body = f"see the {commitcheck.OVERRIDE_START} mechanism for details"
    assert commitcheck.override_block(body) == "mechanism for details"
    assert commitcheck.preprocess_commit_message("fix: a subject", body) == "mechanism for details"


def test_a_closed_block_stops_at_the_closing_marker():
    body = (
        f"prose\n{commitcheck.OVERRIDE_START}\nfix: the real subject\n"
        f"{commitcheck.OVERRIDE_END}\nmore prose\n"
    )
    assert commitcheck.override_block(body) == "fix: the real subject"


def test_an_empty_block_is_not_an_override():
    """Upstream's `if (overrideMessage)` is falsy on an empty string.

    A body that ends on the marker leaves nothing after it, so release-please
    falls back to the commit message and nothing is lost. Reporting that as a
    failure would be crying wolf at the one shape that is harmless.
    """
    body = f"the marker is {commitcheck.OVERRIDE_START}"
    assert commitcheck.override_block(body) is None
    assert commitcheck.preprocess_commit_message("fix: a subject", body) == "fix: a subject"
    assert commitcheck.override_faults(body) == []


def test_the_first_marker_wins():
    """`split(...)[1]` is the text between the first and second occurrence."""
    marker = commitcheck.OVERRIDE_START
    body = f"a {marker} first block {marker} second block"
    assert commitcheck.override_block(body) == "first block"


def test_no_body_at_all_leaves_the_message_alone():
    for body in (None, "", "ordinary prose with no marker in it"):
        assert commitcheck.preprocess_commit_message("fix: a subject", body) == "fix: a subject"
        assert commitcheck.override_faults(body) == []


def test_an_unclosed_block_is_refused_even_when_the_prose_parses():
    """This repository is stricter than upstream here, on purpose.

    Upstream reads an unclosed block to the end of the body and is content. That
    is exactly the shape an accidental mention takes, so a mention whose next
    paragraph happens to parse would otherwise become the changelog entry with
    nothing said. Requiring the closing marker costs a deliberate override one
    line and makes the intent visible.
    """
    body = f"see {commitcheck.OVERRIDE_START}\n\nfix: this parses by luck\n"
    assert not commitcheck.check_text(commitcheck.override_block(body), engine="python")
    faults = commitcheck.override_faults(body)
    assert faults, "an unclosed block that parses is still an accident"
    assert "never" in " ".join(faults)


def test_a_deliberate_closed_override_is_left_alone():
    body = (
        f"## Intent\n\nprose\n\n{commitcheck.OVERRIDE_START}\n"
        f"fix(toon): restate the dropped fix\n\nbody\n{commitcheck.OVERRIDE_END}\n"
    )
    problems, faults = commitcheck.check_pull_request("chore: internal title", 12, body)
    assert not problems and not faults


# ---------------------------------------------------------------------------
# The artefacts a merge creates, which no commit-msg hook ever sees.
# ---------------------------------------------------------------------------


def test_the_pull_request_check_would_have_caught_it_before_the_merge(hijacked_body):
    """The merge-time half of the fix, against the body that actually did it."""
    problems, faults = commitcheck.check_pull_request(
        "ci: prevent release workflow from silently skipping unparseable commits",
        11,
        hijacked_body,
    )
    assert faults, "an unclosed accidental override is a fault in its own right"
    assert [str(p.error) for p in problems] == [HIJACKED_ERROR]
    assert problems[0].artefact == commitcheck.OVERRIDE_ARTEFACT


def test_the_title_is_checked_because_github_offers_it_as_the_squash_subject():
    problems, faults = commitcheck.check_pull_request("prevent the workflow skipping", 12, "prose")
    assert not faults
    assert [p.artefact for p in problems] == [commitcheck.TITLE_ARTEFACT]
    # And a conventional title with the number GitHub appends is fine.
    assert not commitcheck.check_pull_request("fix(ci): a subject", 12, "prose")[0]


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
@pytest.mark.parametrize("label", sorted(commitcheck.DEMO_PULL_REQUESTS_REJECTED))
def test_every_unmergeable_pull_request_shape_is_refused(label, engine):
    title, number, body = commitcheck.DEMO_PULL_REQUESTS_REJECTED[label]
    problems, faults = commitcheck.check_pull_request(title, number, body, engine=engine)
    assert problems or faults, label


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
@pytest.mark.parametrize("label", sorted(commitcheck.DEMO_PULL_REQUESTS_ACCEPTED))
def test_every_legitimate_pull_request_shape_is_accepted(label, engine):
    title, number, body = commitcheck.DEMO_PULL_REQUESTS_ACCEPTED[label]
    problems, faults = commitcheck.check_pull_request(title, number, body, engine=engine)
    assert not problems and not faults, f"{label}: {problems or faults}"


# ---------------------------------------------------------------------------
# The corpus: what must be refused, and what must keep working.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
@pytest.mark.parametrize("label", sorted(commitcheck.DEMO_REJECTED))
def test_every_unparseable_shape_is_refused(label, engine):
    assert commitcheck.check(commitcheck.DEMO_REJECTED[label], engine=engine), label


@pytest.mark.parametrize("engine", ENGINE_PARAMS)
@pytest.mark.parametrize("label", sorted(commitcheck.DEMO_ACCEPTED))
def test_every_legitimate_shape_is_accepted(label, engine):
    problems = commitcheck.check(commitcheck.DEMO_ACCEPTED[label], engine=engine)
    assert not problems, f"{label}: {problems[0].error if problems else ''}"


def test_the_rule_is_the_interaction_and_not_any_one_ingredient():
    """Parentheses, backticks, dashes and position are each fine on their own.

    This is the assertion that stops the guard drifting into a ban on rich
    bodies. The failing shape is a line-initial token run into a parenthesis
    that does not close cleanly; every ingredient of it, alone, is legitimate.
    """
    body = "fix: a subject\n\n{}\n"
    for innocent in (
        "parentheses (like these) mid-line",
        "`backticks` around a term",
        "an em-dash - used as a dash",
        "call(argument) at the line start",
        "call() at the line start",
        "call(a) and call(b) at the line start",
    ):
        assert not commitcheck.check(body.format(innocent)), innocent
    assert commitcheck.check(body.format("call(nested(argument))")), "the interaction must fail"


def test_the_demo_is_a_self_test_that_can_fail(capsys):
    """`--demo` has to be able to report a wrong verdict, or it proves nothing."""
    assert commitcheck.run_demo("python", stream=sys.stdout) == 0
    original = commitcheck.DEMO_REJECTED.copy()
    try:
        commitcheck.DEMO_REJECTED["a message that parses fine"] = "fix: a subject\n"
        assert commitcheck.run_demo("python", stream=sys.stdout) == 1
    finally:
        commitcheck.DEMO_REJECTED.clear()
        commitcheck.DEMO_REJECTED.update(original)
    assert "accepted a message it must reject" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The agreement between the transcription and the parser it stands in for.
# ---------------------------------------------------------------------------


def _corpus():
    cases = {f"reject:{k}": v for k, v in commitcheck.DEMO_REJECTED.items()}
    cases.update({f"accept:{k}": v for k, v in commitcheck.DEMO_ACCEPTED.items()})
    cases["lost-commit"] = (MESSAGES / "sibling-41bcb73.txt").read_text(encoding="utf-8")
    cases["lucky-commit"] = (MESSAGES / "46c25f9.txt").read_text(encoding="utf-8")
    # Shapes with no verdict recorded anywhere else: the point of running them is
    # that the two engines must agree about whatever the answer turns out to be.
    cases["empty"] = ""
    cases["subject only"] = "fix: a subject"
    cases["subject and no blank line"] = "fix: a subject\nbody straight after"
    cases["crlf line endings"] = "fix: a subject\r\n\r\n`a(b(c))` at the line start\r\n"
    cases["a leading blank line"] = "\n\n\nfix: a subject\n\n`a(b(c))` here\n"
    cases["trailing whitespace only"] = "fix: a subject\n\nbody\n\n   \n"
    cases["a non-breaking space before the paren"] = "fix: a subject\n\ncall\u00a0(nested(x))\n"
    cases["nested commit block"] = (
        "chore: outer\n\nbody\n\nBEGIN_NESTED_COMMIT\nfix: inner\n\n`a(b(c))` here\n"
        "END_NESTED_COMMIT\n"
    )
    cases["a bare 'BREAKING CHANGE' body line"] = "fix: a subject\n\nBREAKING CHANGE: it moved\n"
    return cases


CORPUS = _corpus()


@NODE
@pytest.mark.parametrize("label", sorted(CORPUS))
def test_the_transcription_agrees_with_the_vendored_parser(label):
    """Same verdict, same position, same token -- for every case in the corpus.

    Compared field by field rather than on the rendered string, so a difference
    in where the failure was found cannot hide behind identical wording.
    """
    message = CORPUS[label]
    ported = commitcheck.check(message, engine="python")
    upstream = commitcheck.check(message, engine="node")
    assert [(p.line, p.column, p.error.found, p.error.valid, str(p.error)) for p in ported] == [
        (p.line, p.column, p.error.found, p.error.valid, str(p.error)) for p in upstream
    ], label


@NODE
def test_the_corpus_actually_exercises_both_outcomes():
    """A corpus that only passes proves the engines agree about nothing."""
    verdicts = {bool(commitcheck.check(m, engine="node")) for m in CORPUS.values()}
    assert verdicts == {True, False}


# ---------------------------------------------------------------------------
# The vendored parser itself.
# ---------------------------------------------------------------------------


def test_the_vendored_parser_is_unmodified():
    """A file edited to make the transcription look right is not upstream's opinion."""
    recorded = {}
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        recorded[name] = digest
    assert recorded, "checksums.txt is empty"
    for name, digest in recorded.items():
        actual = hashlib.sha256((VENDOR_ROOT / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} differs from the vendored copy"


def test_the_vendored_parser_needs_nothing_outside_its_own_directory():
    """`node` alone has to run it -- no `npm install`, no network, no lockfile."""
    for source in sorted((VENDOR_ROOT / "lib").glob("*.js")):
        for line in source.read_text(encoding="utf-8").splitlines():
            if "require(" in line:
                assert "require('./" in line, f"{source.name}: {line.strip()}"


def test_the_number_of_ways_a_message_can_be_refused_is_pinned():
    """Four `throw`s in the grammar, and the transcription was written against those.

    A refreshed parser with a fifth has grown a rejection this file has never
    seen, and the port has to be re-read rather than assumed still faithful.
    """
    grammar = (VENDOR_ROOT / "lib" / "parser.js").read_text(encoding="utf-8")
    throws = [line for line in grammar.splitlines() if line.strip().startswith("throw ")]
    assert len(throws) == commitcheck.THROW_SITES
    # And the transcription cites every one of them by upstream line number.
    ported = Path(commitcheck.__file__).read_text(encoding="utf-8")
    for number in (17, 30, 48, 177):
        assert f"lib/parser.js:{number}" in ported


# ---------------------------------------------------------------------------
# The release audit, and the allowance that keeps it honest.
# ---------------------------------------------------------------------------


def _repo(tmp_path, commits):
    """A throwaway repository with one commit per message, oldest first."""
    root = tmp_path / "repo"
    root.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "nobody@example.com")
    run("config", "user.name", "Nobody")
    (root / ".release-please-manifest.json").write_text('{\n  ".": "1.0.0"\n}\n')
    run("add", ".release-please-manifest.json")
    run("commit", "-q", "-m", "chore: release 1.0.0")
    run("tag", "v1.0.0")
    for message in commits:
        run("commit", "-q", "--allow-empty", "-m", message)
    return root


def test_the_audit_passes_when_every_commit_can_be_read(tmp_path, capsys):
    root = _repo(tmp_path, ["fix: a real fix\n\nbody text\n"])
    assert (
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="skip")
        == 0
    )
    assert "silently dropped:              0" in capsys.readouterr().out


def test_the_audit_fails_when_a_commit_would_be_dropped(tmp_path, capsys):
    root = _repo(tmp_path, ["fix: a real fix\n\n`a(b(c))` at the line start\n"])
    assert (
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="skip")
        == 1
    )
    out = capsys.readouterr().out
    assert "silently dropped:              1" in out


def test_zero_considered_with_commits_waiting_is_called_out_by_name(tmp_path, capsys):
    """The exact state that exited 0 and shipped nothing.

    A run that considers zero commits while commits are waiting is not the same
    as a run with nothing to do, and the whole failure was that the two looked
    identical.
    """
    root = _repo(tmp_path, ["fix: a real fix\n\n`a(b(c))` at the line start\n"])
    assert (
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="skip")
        == 1
    )
    out = capsys.readouterr().out
    assert "release-please would consider: 0" in out
    assert "ZERO of these commits and still" in out


def test_an_empty_range_is_not_reported_as_a_failure(tmp_path, capsys):
    """The healthy no-op still has to pass, or the guard gets switched off."""
    root = _repo(tmp_path, [])
    assert (
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="skip")
        == 0
    )
    assert "ZERO" not in capsys.readouterr().out


def test_since_release_reads_the_range_from_the_manifest(tmp_path):
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    assert commitcheck.released_version(root) == "1.0.0"
    assert len(commitcheck.commits_since("v1.0.0..HEAD", root=root)) == 1


@pytest.mark.parametrize("sha", sorted(commitcheck.KNOWN_UNPARSEABLE))
def test_every_known_unparseable_entry_is_still_earning_its_place(sha):
    """An allowance whose commit now parses is covering something else.

    Same discipline as `PATH_ALLOWANCES` in the leak scanner: the entry has to
    keep being true, or it stops being an exemption and becomes a blind spot.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%B", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("the commit is not reachable from this checkout")
    assert commitcheck.check(result.stdout, engine="python"), (
        f"{sha[:12]} parses now -- delete the allowance rather than leaving it"
    )
    assert commitcheck.KNOWN_UNPARSEABLE[sha].strip(), "an allowance must say why it is there"


def test_the_allowance_is_matched_on_the_full_sha(tmp_path):
    """A prefix is not an identifier: two commits can share one.

    The audit looks the SHA up in a dictionary, so this is a statement about
    that choice rather than a bug that was found -- but the leak scanner's
    trailing-match defect was exactly this shape, one layer down.
    """
    root = _repo(tmp_path, ["fix: a real fix\n\n`a(b(c))` at the line start\n"])
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    original = commitcheck.KNOWN_UNPARSEABLE.copy()
    try:
        commitcheck.KNOWN_UNPARSEABLE[head[:12]] = "a prefix, which must not exempt anything"
        assert (
            commitcheck.audit_range(
                "v1.0.0..HEAD", engine="python", root=root, pull_requests="skip"
            )
            == 1
        )
        commitcheck.KNOWN_UNPARSEABLE[head] = "the full SHA, which must"
        assert (
            commitcheck.audit_range(
                "v1.0.0..HEAD", engine="python", root=root, pull_requests="skip"
            )
            == 0
        )
    finally:
        commitcheck.KNOWN_UNPARSEABLE.clear()
        commitcheck.KNOWN_UNPARSEABLE.update(original)


def test_the_rules_output_shows_the_allowances(capsys):
    """An exemption nobody can see is one nobody re-examines."""
    assert commitcheck.list_rules("python", stream=sys.stdout) == 0
    out = capsys.readouterr().out
    assert "known-unparseable commits" in out
    for sha in commitcheck.KNOWN_UNPARSEABLE:
        assert sha in out
    if not commitcheck.KNOWN_UNPARSEABLE:
        assert "(none)" in out


# ---------------------------------------------------------------------------
# The audit's reach: it has to read the half of the artefact that is not in git.
# ---------------------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch):
    """No credential and no repository, so nothing here can reach the network."""
    monkeypatch.setattr(commitcheck, "github_token", lambda: None)
    monkeypatch.setattr(commitcheck, "repo_slug", lambda root=".": None)


def _with_bodies(monkeypatch, bodies):
    """A reachable repository whose commits carry `bodies`. No network."""
    monkeypatch.setattr(commitcheck, "github_token", lambda: "a token")
    monkeypatch.setattr(commitcheck, "repo_slug", lambda root=".": "owner/name")
    monkeypatch.setattr(commitcheck, "github_json", lambda path, **kwargs: {"full_name": path})
    monkeypatch.setattr(
        commitcheck,
        "pull_request_body_for_commit",
        lambda sha, **kwargs: bodies.get(sha),
    )


def test_the_audit_fails_on_a_perfect_message_whose_body_replaces_it(tmp_path, capsys, monkeypatch):
    """The whole defect, end to end, through the mode the release workflow runs.

    Every commit message in this range is impeccable. The audit must still fail,
    because the pull request body is what release-please will read.
    """
    root = _repo(tmp_path, ["fix: a real fix\n\nbody text\n"])
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    _with_bodies(monkeypatch, {head: f"prose {commitcheck.OVERRIDE_START} block from the body"})
    assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 1
    out = capsys.readouterr().out
    assert "1 carry a commit-override block" in out
    assert "release-please would consider: 0" in out


def test_the_audit_says_so_when_it_could_not_read_the_bodies(tmp_path, capsys, offline):
    """A reduced check that reads as a full one is how this failed the first time."""
    root = _repo(tmp_path, ["fix: a real fix\n\nbody text\n"])
    assert commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root) == 0
    out = capsys.readouterr().out
    assert "NOT consulted" in out
    assert "not necessarily the text" in out


def test_require_refuses_to_report_a_verdict_it_cannot_support(tmp_path, offline):
    """What the workflows run. Without the bodies there is no answer, not a pass."""
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    with pytest.raises(commitcheck.GitHubUnavailable):
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="require")


def test_skip_is_the_only_way_to_get_the_old_reach_and_it_says_so(tmp_path, capsys, monkeypatch):
    _with_bodies(monkeypatch, {})
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    assert (
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="skip")
        == 0
    )
    assert "not consulted" in capsys.readouterr().out


def test_a_commit_github_does_not_have_is_an_answer_and_not_an_outage(
    tmp_path, capsys, monkeypatch
):
    """Unpushed work is the ordinary local state, and has no pull request.

    Failing on it would make the audit unusable on a branch, which is where it
    is most useful. The commits are still named in the output, because "we did
    not look" and "there was nothing to look at" must not read the same.
    """
    monkeypatch.setattr(commitcheck, "github_token", lambda: "a token")
    monkeypatch.setattr(commitcheck, "repo_slug", lambda root=".": "owner/name")

    def answer(path, **kwargs):
        if path == "/repos/owner/name":
            return {"full_name": "owner/name"}
        raise commitcheck.GitHubMissing(f"GET {path}: HTTP Error 422")

    monkeypatch.setattr(commitcheck, "github_json", answer)
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    assert (
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="require")
        == 0
    )
    assert "are not on owner/name yet" in capsys.readouterr().out


def test_a_credential_that_cannot_reach_the_repository_is_not_an_all_clear(tmp_path, monkeypatch):
    """The blind spot arrived at from the other direction.

    A token without access answers 404 for every commit. Read naively, that is
    "no pull request anywhere" -- which is exactly the wrong string being
    checked again, with nothing said. So the repository is reached once first,
    and a failure there is an outage.
    """
    monkeypatch.setattr(commitcheck, "github_token", lambda: "a token")
    monkeypatch.setattr(commitcheck, "repo_slug", lambda root=".": "owner/name")

    def answer(path, **kwargs):
        raise commitcheck.GitHubMissing(f"GET {path}: HTTP Error 404")

    monkeypatch.setattr(commitcheck, "github_json", answer)
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    with pytest.raises(commitcheck.GitHubUnavailable):
        commitcheck.audit_range("v1.0.0..HEAD", engine="python", root=root, pull_requests="require")


def test_the_command_line_reports_a_missing_credential_rather_than_passing(tmp_path):
    """The exit code has to be non-zero: a check that cannot run has not passed."""
    root = _repo(tmp_path, ["fix: a real fix\n\nbody\n"])
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    env["GITHUB_REPOSITORY"] = ""
    # `git` but no `gh`: the script still has to read the range, and must not
    # borrow a credential from whatever the developer happens to be logged into.
    env["PATH"] = str(Path(shutil.which("git")).parent)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "commitcheck.py"),
            "--range",
            "v1.0.0..HEAD",
            "--pull-requests",
            "require",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 1
    assert "Refusing to report a verdict" in result.stderr


# ---------------------------------------------------------------------------
# The hook, end to end.
# ---------------------------------------------------------------------------


def test_the_release_audit_requires_the_pull_request_bodies():
    """Wired with `require`, or the workflow silently checks the wrong string.

    `auto` would degrade to git's copy of the messages the moment a token went
    missing, and report success -- which is the exact failure this change fixes.
    """
    # ci.yml delegates the audit to scripts/ci-local.sh, so the flag lives there.
    local = (REPO_ROOT / "scripts" / "ci-local.sh").read_text(encoding="utf-8")
    for name in ("release.yml", "ci.yml"):
        workflow = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        if name == "ci.yml":
            assert "scripts/ci-local.sh --only commits" in workflow, name
            workflow += local
        assert "--since-release --pull-requests require" in workflow, name
        assert "GITHUB_TOKEN" in workflow, name


def test_a_pull_request_is_checked_before_it_can_be_merged():
    """The body can be edited after every other check has run, so `edited` counts."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "hygiene.yml").read_text(encoding="utf-8")
    assert "--pull-request" in workflow
    assert "edited" in workflow


def test_the_commit_msg_hook_runs_this_check():
    """Wired, not merely available. An unwired guard reads exactly like a passing one."""
    hook = (REPO_ROOT / ".githooks" / "commit-msg").read_text(encoding="utf-8")
    assert "commitcheck.py" in hook
    assert "--commit-msg" in hook


def test_the_script_exits_non_zero_on_the_lost_message(tmp_path):
    target = tmp_path / "COMMIT_EDITMSG"
    target.write_bytes((MESSAGES / "sibling-41bcb73.txt").read_bytes())
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "commitcheck.py"),
            "--commit-msg",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert LOST_ERROR in result.stderr


def test_git_comment_lines_are_not_part_of_the_message(tmp_path):
    """git strips them before the commit exists, so refusing one is unsatisfiable.

    The template git writes into `COMMIT_EDITMSG` contains parentheses, and a
    guard that failed on the instructions it was given would be uncloseable.
    """
    message = "fix: a subject\n\nbody\n\n# Please enter the commit message (or leave it empty).\n"
    target = tmp_path / "COMMIT_EDITMSG"
    target.write_text(message, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "commitcheck.py"),
            "--commit-msg",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
