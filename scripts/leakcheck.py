#!/usr/bin/env python3
"""Block installation-specific data from entering this public repository.

hass-axi talks to home automation installations, so the failure mode that matters
is not a bug -- it is a commit that quietly describes, or grants access to, the
installation it was developed against. A rule a human has to remember is not a
control, so this scanner runs from a pre-commit hook, a commit-msg hook, and CI.

It looks for shapes rather than a denylist of known-bad strings, because the
strings that matter are the ones nobody thought to list. See RULES below for the
current set.

Two passes run over every file:

* a line pass, which reports the exact line; and
* a condensed pass, which strips whitespace, quotes, backslashes and ``+`` from
  the whole file before re-applying the token rules. A credential split over
  several source lines, or assembled by concatenation, is invisible to a line
  pass -- and splitting a token over fragments is exactly how someone hides one,
  deliberately or not. The condensed pass is restricted to the token rules
  because joining arbitrary lines can fuse unrelated digits into a plausible
  address, and a guard that cries wolf gets bypassed.

Three surfaces reach a public page, and only two of them are files. A pull
request's title and body are published the moment they are written, are in no
checkout, pass under no hook, and can be edited after every other check has run
-- and this project's own pipeline writes into the body, pasting captured pytest
output whose header carries a ``rootdir:`` line holding an absolute path. That
has now happened twice, on two repositories, with every check green both times.
``--pull-request`` is the surface's own scan: the same rules, read from GitHub at
check time, reported without ever echoing what it found.

Usage:
  scripts/leakcheck.py                     scan every tracked file
  scripts/leakcheck.py --staged            scan the staged content of a commit
  scripts/leakcheck.py --commit-msg PATH   scan a commit message
  scripts/leakcheck.py --pull-request N    scan a pull request's title and body
  scripts/leakcheck.py PATH [PATH...]      scan explicit files or directories
  scripts/leakcheck.py --demo              scan a synthetic dirty tree and expect failure
  scripts/leakcheck.py --rules             list the rules and what each one catches

A line may carry `leakcheck: allow=<rule>[,<rule>]` to exempt itself from those
named rules. The exemption is deliberately per-rule: a blanket marker would
switch off every rule on the line, including one nobody was thinking about when
they wrote it, which is how a live credential hides behind a suppressed lint.

PATH_ALLOWANCES says the same thing for a file that cannot carry a marker at
all. It is per-path AND per-rule for the same reason, and `--rules` prints it,
because an exemption nobody can see is one nobody re-examines.

Standard library only, so the hooks run without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ALLOW_PREFIX = "leakcheck: allow="

#: Documentation-reserved domains (RFC 2606, RFC 6761). The local part of an
#: address is deliberately NOT consulted: `noreply@` on a real domain still
#: identifies a real organisation.
_ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "example",
    "invalid",
    "test",
    "localhost",
)


#: Trailers whose whole purpose is to carry a person's identity. Git already
#: records author and committer addresses in every commit, so flagging these
#: would block ordinary attribution without preventing anything -- and a guard
#: that blocks routine commits is a guard people learn to bypass. The same
#: applies to a pull request body, which GitHub's squash box offers as the
#: commit message.
_IDENTITY_TRAILER = re.compile(
    r"^(?:co-authored-by|signed-off-by|reported-by|reviewed-by|acked-by|tested-by"
    r"|suggested-by|helped-by|author|committer|cc)\s*:",
    re.IGNORECASE,
)

#: What an identity trailer is exempt from: the address it exists to carry, and
#: nothing else. A trailer is not a place to smuggle an address or a token.
_TRAILER_EXEMPT = frozenset({"personal-email"})


class Rule:
    """One detectable shape, and the guidance printed when it fires."""

    def __init__(self, name, pattern, message, allow=None, condensed=False):
        self.name = name
        self.pattern = re.compile(pattern)
        self.message = message
        self.allow = allow or (lambda match: False)
        #: Whether this rule also runs over the condensed whole-file view.
        self.condensed = condensed

    def scan(self, text):
        for match in self.pattern.finditer(text):
            if not self.allow(match):
                yield match


def _email_allowed(match):
    domain = match.group(2).lower()
    return any(
        domain == allowed or domain.endswith("." + allowed) for allowed in _ALLOWED_EMAIL_DOMAINS
    )


def _bearer_allowed(match):
    """Keep the bearer rule off prose and placeholders.

    The pattern restricts the value to the base64/hex alphabet, which excludes
    `Bearer <token>` and `Bearer $HA_TOKEN`. What is left to exclude is English
    prose, so a real value must also carry a digit, which encoded bytes do.
    """
    return not any(character.isdigit() for character in match.group(1))


def _hex_id_allowed(match):
    """Keep the MAC rule off content-addressed hex such as a git object id."""
    return match.group(0).count(":") not in (5, 7)


RULES = [
    Rule(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}",
        "a JSON Web Token, which is what a Home Assistant long-lived access token looks like",
        condensed=True,
    ),
    Rule(
        "bearer",
        r"[Bb]earer\s+([A-Za-z0-9._~+/=-]{16,})",
        "a literal bearer credential",
        allow=_bearer_allowed,
        condensed=False,
    ),
    Rule(
        "private-ip",
        r"\b(?:192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
        "an RFC1918 private address, which describes somebody's network",
    ),
    Rule(
        "cgnat-ip",
        r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
        "a carrier-grade NAT address (the 100.64/10 range), a common remote-access overlay",
    ),
    Rule(
        "link-local-ip",
        r"\b169\.254\.\d{1,3}\.\d{1,3}\b",
        "an IPv4 link-local address, which describes somebody's network",
    ),
    Rule(
        "private-ipv6",
        r"\b(?:[fF][cCdD][0-9a-fA-F]{2}|[fF][eE]80):[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){1,7}",
        "a unique-local or link-local IPv6 address, which describes somebody's network",
    ),
    Rule(
        "remote-ui-host",
        r"\b[0-9a-z]{8,}\.ui\.nabu\.casa\b",
        "a per-installation remote-access hostname, which grants a route to somebody's home",
    ),
    Rule(
        "lan-hostname",
        r"\b[A-Za-z0-9][A-Za-z0-9-]*\.(?:local|lan|localdomain)\b(?![.\w])",
        "a LAN or mDNS hostname, which names somebody's machine",
    ),
    Rule(
        "coordinates",
        r"(?i)\b(?:latitude|longitude|\blat\b|\blon\b|\blng\b)\W{0,4}[-+]?\d{1,3}\.\d{3,}",
        "a geographic coordinate; /api/config returns the coordinates of the house",
    ),
    Rule(
        "mac-address",
        r"\b(?:[0-9a-fA-F]{2}:){5,7}[0-9a-fA-F]{2}\b",
        "a MAC or Zigbee IEEE address, which uniquely identifies somebody's hardware",
        allow=_hex_id_allowed,
    ),
    Rule(
        "home-path",
        r"(?:/home/|/Users/)[A-Za-z][A-Za-z0-9._-]*(?:/|\b)"
        r"|[A-Za-z]:\\\\?Users\\\\?[A-Za-z][A-Za-z0-9._-]*",
        "an absolute home directory, which names a person and a machine",
    ),
    Rule(
        "personal-email",
        r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
        "an email address outside the reserved documentation domains",
        allow=_email_allowed,
    ),
]

RULES_BY_NAME = {rule.name: rule for rule in RULES}

#: Files exempt from one named rule each, for content that cannot carry a
#: `leakcheck: allow=` marker. These files are commit messages kept
#: byte-for-byte as evidence -- editing one to satisfy this scanner would leave
#: a likeness of the message where the regression needs the real thing. Scoped
#: exactly like the per-line marker: one path, one rule, every other rule still
#: runs.
PATH_ALLOWANCES = {
    # The sibling project's commit message that release-please could not read,
    # kept byte-for-byte so the regression is the real thing rather than a
    # likeness of it. Its co-author trailer carries a no-reply address, which
    # --commit-msg mode already permits as an identity trailer; the same bytes
    # stored as a file reach the tracked-files scan instead, and the file cannot
    # carry a marker without ceasing to be the message.
    "tests/fixtures/commit-messages/sibling-41bcb73.txt": frozenset({"personal-email"}),
    # This repository's own commit describing the same fix, kept beside it because
    # the pair is the evidence: same prose, same term, and only one of them
    # parses. Same trailer, same reason it cannot carry a marker.
    "tests/fixtures/commit-messages/46c25f9.txt": frozenset({"personal-email"}),
    # The sibling project's commit whose message was fine and was not the one
    # release-please read: the body of its pull request replaced it. Kept for the
    # same reason as the two above, and exempted on the same grounds -- the same
    # no-reply co-author trailer, and the same requirement that the bytes stay
    # exactly the commit's.
    "tests/fixtures/commit-messages/sibling-b1f9bb18.txt": frozenset({"personal-email"}),
}


def path_allowances(path):
    """Rule names ``path`` is exempt from.

    Compared exactly against the repository-relative name the caller holds. A
    path that merely ends with an allowed one is a different file -- a shadowing
    directory, a suffixed twin -- and exempting it would grant the entry every
    directory it is ever copied into.
    """
    return PATH_ALLOWANCES.get(str(path), frozenset())


SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".whl",
    ".woff",
    ".woff2",
    ".ttf",
    ".mo",
    ".pyc",
    ".so",
}

#: Characters removed to build the condensed view: source syntax that can sit
#: between two halves of one secret without changing what it is.
_CONDENSE = re.compile(r"[\s\"'`\\+,]")


class Finding:
    def __init__(self, path, line_number, rule, excerpt, matched, pass_name="line", column=None):
        self.path = path
        self.line_number = line_number
        self.rule = rule
        self.excerpt = excerpt
        self.matched = matched
        self.pass_name = pass_name
        #: 1-based offset into the line, when the pass that found it read the
        #: line as written. A finding from a transformed view -- the
        #: percent-decoded variant, or the condensed pass -- carries none: no
        #: offset into those views points at anything the reader can open, and a
        #: number printed as though it did would send somebody to a position
        #: where nothing is. Carried so a finding can be located without the
        #: excerpt being printed, which is what the pull request report needs:
        #: a public CI log must say where the match is without republishing it.
        self.column = column

    @property
    def key(self):
        # Keyed on the matched value, not the excerpt: the ledger the line pass
        # and the condensed pass share, so the same secret seen by both -- with
        # different surroundings, maybe split across lines the line pass could
        # not read -- is one leak and not two. It deliberately does not dedupe
        # across lines: the same value on two lines is two places to fix, and
        # the pull request report prints no excerpt, so a withheld second line
        # is a finding that resurfaces one CI run later.
        return (self.path, self.rule.name, self.matched)


def allowed_rules(line):
    """Rule names this line exempts itself from."""
    index = line.find(ALLOW_PREFIX)
    if index < 0:
        return frozenset()
    tail = line[index + len(ALLOW_PREFIX) :]
    names = re.match(r"[A-Za-z0-9_,-]+", tail.strip())
    if not names:
        return frozenset()
    return frozenset(part for part in names.group(0).split(",") if part)


def line_exemptions(line, *, markers=True, trailers=False):
    """Rule names ``line`` is exempt from, under the caller's policy.

    ``markers`` is the per-line `leakcheck: allow=` escape hatch, and it is off
    for a pull request. That marker is committed, diffed and reviewed when it
    sits in a file; in a pull request body it is an off-switch anyone with write
    access can add after every check has run, on the one artefact whose being
    editable-after-the-fact is the reason this surface needs a guard at all. A
    body that must describe a shape can describe it instead of spelling it out.

    ``trailers`` exempts an attribution trailer from the address rule it exists
    to carry -- and from nothing else.
    """
    exempt = allowed_rules(line) if markers else frozenset()
    if trailers and _IDENTITY_TRAILER.match(line.strip()):
        exempt = exempt | _TRAILER_EXEMPT
    return exempt


def _decoded_variants(text):
    """The text plus a percent-decoded view, so encoded tokens still register."""
    variants = [text]
    if "%" in text:
        try:
            decoded = urllib.parse.unquote(text)
        except (UnicodeDecodeError, ValueError):
            decoded = ""
        if decoded and decoded != text:
            variants.append(decoded)
    return variants


def scan_text(path, text, *, markers=True, trailers=False):
    """Scan one text with both the line pass and the condensed pass.

    ``markers`` and ``trailers`` are the exemption policy; see
    ``line_exemptions``. The rules themselves never vary by surface -- a file, a
    commit message and a pull request body are all read by one rule set, so a
    rule added later covers all three without anyone remembering to wire it up.
    """
    findings = []
    #: The cross-pass ledger: a value the line pass reported is one leak, and
    #: the condensed pass checks itself against this so it does not report the
    #: same secret again from a view with different surroundings.
    seen = set()
    #: Repeats within one line. The same value twice on one line is still one
    #: finding, but the same value on two lines is two places to fix and both
    #: must be named: the pull request report has no excerpt, so a line number
    #: is the whole locator, and a withheld second line becomes a fresh red run
    #: the moment the first is fixed.
    on_this_line = set()
    by_path = path_allowances(path)

    for number, line in enumerate(text.splitlines(), start=1):
        exempt = line_exemptions(line, markers=markers, trailers=trailers) | by_path
        for viewed, variant in enumerate(_decoded_variants(line)):
            for rule in RULES:
                if rule.name in exempt:
                    continue
                for match in rule.scan(variant):
                    finding = Finding(
                        path,
                        number,
                        rule,
                        _excerpt(variant, match),
                        match.group(0),
                        pass_name="line" if viewed == 0 else "decoded",
                        column=match.start() + 1 if viewed == 0 else None,
                    )
                    place = (*finding.key, number)
                    if place in on_this_line:
                        continue
                    on_this_line.add(place)
                    seen.add(finding.key)
                    findings.append(finding)

    findings.extend(_scan_condensed(path, text, seen, by_path, markers=markers, trailers=trailers))
    findings.sort(key=lambda f: (f.line_number, f.rule.name))
    return findings


def _scan_condensed(path, text, seen, by_path, *, markers=True, trailers=False):
    """Re-scan the whole file with source-level separators removed."""
    condensed_chars = []
    line_of = []
    line_number = 1
    for character in text:
        if character == "\n":
            line_number += 1
            continue
        if _CONDENSE.match(character):
            continue
        condensed_chars.append(character)
        line_of.append(line_number)
    condensed = "".join(condensed_chars)
    if not condensed:
        return []

    findings = []
    for variant, offsets in _condensed_variants(condensed, line_of):
        for rule in RULES:
            if not rule.condensed:
                continue
            for match in rule.scan(variant):
                start = offsets[match.start()] if match.start() < len(offsets) else 1
                if rule.name in by_path or _line_is_exempt(
                    text, start, rule.name, markers=markers, trailers=trailers
                ):
                    continue
                finding = Finding(
                    path,
                    start,
                    rule,
                    _excerpt(variant, match),
                    match.group(0),
                    pass_name="joined",
                )
                if finding.key not in seen:
                    seen.add(finding.key)
                    findings.append(finding)
    return findings


def _condensed_variants(condensed, line_of):
    variants = [(condensed, line_of)]
    if "%" in condensed:
        decoded = urllib.parse.unquote(condensed)
        if decoded != condensed:
            # Offsets shift once characters are decoded; attribute the whole
            # match to the first line of the condensed region instead.
            variants.append((decoded, [line_of[0] if line_of else 1] * (len(decoded) + 1)))
    return variants


def _line_is_exempt(text, line_number, rule_name, *, markers=True, trailers=False):
    lines = text.splitlines()
    if 1 <= line_number <= len(lines):
        return rule_name in line_exemptions(
            lines[line_number - 1], markers=markers, trailers=trailers
        )
    return False


def _excerpt(line, match):
    start = max(match.start() - 12, 0)
    end = min(match.end() + 12, len(line))
    text = line[start:end].strip()
    return (text[:100] + "...") if len(text) > 100 else text


def _readable(path):
    return Path(path).suffix.lower() not in SKIP_SUFFIXES


def _decode(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def tracked_files(root):
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def walk_files(target):
    target = Path(target)
    if target.is_file():
        yield target
        return
    for base, directories, names in os.walk(target):
        directories[:] = [d for d in directories if d not in SKIP_DIRECTORIES]
        for name in names:
            yield Path(base) / name


def scan_paths(paths, root="."):
    findings = []
    for path in paths:
        full = Path(root) / path
        if not full.is_file() or not _readable(full):
            continue
        text = _decode(full.read_bytes())
        if text is None:
            continue
        findings.extend(scan_text(str(path), text))
    return findings


#: git's constant hash for the empty tree, used to diff the very first commit,
#: which has no HEAD to compare against.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _staged_names(root):
    has_head = (
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"]
    if not has_head:
        command.append(EMPTY_TREE)
    result = subprocess.run(command, cwd=root, capture_output=True, check=True)
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def scan_staged(root="."):
    """Scan the content git would actually record, not the working tree."""
    names = _staged_names(root)
    findings = []
    for name in names:
        if not _readable(name):
            continue
        blob = subprocess.run(
            ["git", "show", f":{name}"], cwd=root, capture_output=True, check=False
        )
        if blob.returncode != 0:
            continue
        text = _decode(blob.stdout)
        if text is None:
            continue
        findings.extend(scan_text(name, text))
    return findings, len(names)


def scan_commit_message(path):
    """Scan a commit message.

    Comment lines (which git strips) are dropped; every other line is scanned
    exactly as file content is, except that an identity trailer is exempt from
    the address rule -- the address, not the line, so a trailer is not a place to
    smuggle anything else.
    """
    text = _decode(Path(path).read_bytes()) or ""
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return scan_text(str(path), "\n".join(lines), trailers=True)


# ---------------------------------------------------------------------------
# The pull request.
#
# The third surface, and the only one that is published before any check has
# looked at it. It is not in the checkout, so the tracked-file scan cannot reach
# it; it never passes under a hook, so --commit-msg cannot either. It is also the
# surface this project's own pipeline writes into: the document step pastes
# captured pytest output, and a pytest header carries a `rootdir:` line holding
# an absolute path. Twice now that has published a home directory -- once here,
# once on the sibling project -- with every check green both times.
#
# The rules are the ones above, unchanged and unforked. What is different is the
# reporting: a public CI log must say WHERE the match is without repeating it,
# so `report_pull_request` prints the field, the line, the offset when it
# locates the match, and the rule -- never the excerpt.
# ---------------------------------------------------------------------------

#: The fields GitHub publishes on a pull request page, in the order they appear.
PULL_REQUEST_FIELDS = ("title", "body")


class PullRequestUnavailable(Exception):
    """The title and body could not be read, so no verdict can be supported."""


def scan_pull_request(fields):
    """Findings in a pull request's published fields, under the same rules.

    ``fields`` is one ``(name, text)`` pair per entry of ``PULL_REQUEST_FIELDS``,
    built once by the reader and shared with the report, so which fields a pull
    request has is written down in one place.

    ``markers=False`` because a pull request cannot carry a credible escape
    hatch; ``trailers=True`` because GitHub's squash box offers the body as the
    commit message and attribution is as routine there as it is in one. See
    ``line_exemptions``.
    """
    findings = []
    for field, text in fields:
        findings.extend(
            scan_text(f"pull request {field}", text or "", markers=False, trailers=True)
        )
    return findings


def _github_transport():
    """The GitHub reader ``commitcheck.py`` already owns.

    That script reads pull request bodies for a different reason -- release-please
    parses one as a commit message -- and its token resolution, slug resolution
    and error taxonomy are already the fail-closed ones this needs. A second copy
    here would be a second thing to keep right, and the two would drift.

    Imported at the point of use so the hooks, which never take this path, do not
    pay for it, and so an import that fails becomes a refusal rather than a
    traceback.
    """
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import commitcheck
    except ImportError as exc:  # pragma: no cover - a broken checkout
        raise PullRequestUnavailable(f"cannot load the GitHub reader: {exc}") from exc
    return commitcheck


def fetch_pull_request(number, *, slug=None, root="."):
    """``(slug, fields)`` for one pull request, or ``PullRequestUnavailable``.

    ``fields`` is one ``(name, text)`` pair per entry of ``PULL_REQUEST_FIELDS``.
    Every failure -- including a failure to resolve the token or the repository,
    which shells out to ``git`` with no guard of its own -- is that exception.
    There is deliberately no path on which this returns empty text: a guard that
    cannot read the artefact must fail the check, because reporting "0 findings"
    for something it never saw is worse than not running -- it converts an
    unknown into an assurance.
    """
    github = _github_transport()
    try:
        token = github.github_token()
        slug = slug or github.repo_slug(root)
    except Exception as exc:
        raise PullRequestUnavailable(
            f"could not resolve a token or repository for pull request {number} ({exc})"
        ) from exc
    if not token:
        raise PullRequestUnavailable(
            "no GitHub token (set GITHUB_TOKEN or GH_TOKEN, or authenticate gh)"
        )
    if not slug:
        raise PullRequestUnavailable("no owner/name for this repository")
    try:
        data = github.pull_request(number, slug=slug, token=token)
    except Exception as exc:  # any failure to read the artefact is a refusal, not a pass
        raise PullRequestUnavailable(f"{slug}#{number} could not be read ({exc})") from exc
    if not isinstance(data, dict):
        raise PullRequestUnavailable(f"{slug}#{number} answered with no pull request")
    return slug, tuple((name, data.get(name) or "") for name in PULL_REQUEST_FIELDS)


def report_pull_request(findings, *, label, fields, stream=None):
    """Say what was found and where, without republishing any of it.

    A pull request check runs on a public log, so the excerpt the file report
    prints is exactly what must not appear here. The field, the line and the
    rule locate the match for the one person who can already see the text, and
    tell nobody else anything they did not have. The offset joins them only
    when the pass read the text as written: a finding from a decoded or joined
    view indexes a string that exists nowhere the reader can open.
    """
    stream = sys.stdout if stream is None else stream
    field_names = {f"pull request {name}": name for name, _ in fields}
    sizes = ", ".join(f"{field} {len(text)} chars" for field, text in fields)
    print(f"leakcheck: {label} ({sizes})", file=stream)
    if not findings:
        print(f"leakcheck: 0 findings in {len(fields)} pull request fields", file=stream)
        return
    print(f"leakcheck[{len(findings)}]{{field,line,offset,rule,pass}}:", file=stream)
    for finding in findings:
        field = field_names.get(finding.path, finding.path)
        offset = finding.column if finding.column is not None else "-"
        print(
            f"  {field},{finding.line_number},{offset},{finding.rule.name},{finding.pass_name}",
            file=stream,
        )
    print("rules:", file=stream)
    for name in sorted({finding.rule.name for finding in findings}):
        print(f"  {name}: {RULES_BY_NAME[name].message}", file=stream)
    print("help:", file=stream)
    print("  The matched text is deliberately NOT printed here: this log is public.", file=stream)
    print(
        "  Open the pull request, go to the line above, and replace the value with a", file=stream
    )
    print(
        "  synthetic one. An offset of `-` means the match was found in a decoded or", file=stream
    )
    print(
        "  joined view of the text, so only the line locates it. Captured tool output", file=stream
    )
    print("  is the usual source -- a pytest header carries a `rootdir:` line holding", file=stream)
    print("  an absolute path.", file=stream)
    print("  Editing the body re-runs this check; nothing else has to happen.", file=stream)
    print(
        f"  A line in a pull request cannot carry `{ALLOW_PREFIX}<rule>`, on purpose.", file=stream
    )


def check_pull_request(number, *, slug=None, root=".", stream=None):
    """Read one pull request and scan it. Any failure to read fails the check."""
    stream = sys.stdout if stream is None else stream
    if not RULES:
        print("leakcheck: no rules loaded; refusing to report a pull request clean", file=stream)
        return 1
    try:
        slug, fields = fetch_pull_request(number, slug=slug, root=root)
    except PullRequestUnavailable as exc:
        print(
            f"leakcheck: cannot read the pull request ({exc}).\n"
            "  A pull request title and body are published the moment they are written and\n"
            "  are in no checkout, so this is the only check that sees them. Refusing to\n"
            "  report a verdict it cannot support.",
            file=stream,
        )
        return 1
    findings = scan_pull_request(fields)
    report_pull_request(findings, label=f"{slug}#{number}", fields=fields, stream=stream)
    return 1 if findings else 0


def _b64(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def synthetic_jwt():
    """Build a structurally valid, entirely fake JWT at run time.

    Encoding it here rather than writing the literal keeps the ``eyJ`` shape out
    of this file, so the scanner's own source stays clean under the condensed
    pass that this fixture exists to exercise.
    """
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64({'sub': 'example'})}.c2lnbmF0dXJlaGVyZQ"


def dirty_fixture():
    """Synthetic dirty content, one file per rule, assembled at run time."""
    token = synthetic_jwt()
    head, payload, signature = token.split(".")
    return {
        "token.md": f"token: {token}\n",
        # The same token, split the way a careless paste or a source literal
        # splits one. Only the condensed pass sees this.
        "split.py": f'TOKEN = (\n    "{head}."\n    "{payload}."\n    "{signature}"\n)\n',
        "encoded.txt": f"u={urllib.parse.quote(token, safe='')}\n",
        "lan.txt": "host " + "192." + "168.1.42:8123\n",
        "cgnat.txt": "peer " + "100." + "101.102.103\n",
        "linklocal.txt": "addr " + "169." + "254.10.20\n",
        "ipv6.txt": "addr " + "fd12" + ":3456:789a::1\n",
        "remoteui.txt": "url https://" + "a1b2c3d4e5f6a7b8" + ".ui.nabu.casa\n",
        "mdns.txt": "url http://" + "homeassistant" + ".local:8123\n",
        "coords.txt": "latitude: " + "51.5" + "0736\n",
        "mac.txt": "mac " + "a4:c1" + ":38:9f:2b:7e\n",
        "run.sh": "source " + "/ho" + "me/" + "someone" + "/.env\n",
        "owner.txt": "contact " + "noreply" + "@" + "realcompany.co.uk\n",
        "curl.md": "curl -H 'Authorization: " + "Bearer " + "abcd1234efgh5678ijkl" + "'\n",
    }


def dirty_pull_request():
    """The leak that has now happened twice, rebuilt from fragments.

    A pipeline step pastes captured pytest output into the body; a pytest header
    carries a ``rootdir:`` line holding an absolute path. Assembled here rather
    than written out so this file stays clean under its own scan, the same way
    ``dirty_fixture`` is. One ``(field, text)`` pair per published field.
    """
    home = "/ho" + "me/" + "someone"
    return (
        ("title", "fix(ci): run the suite from " + home + "/checkout"),
        (
            "body",
            "## Evidence\n\n"
            "<details>\n<summary>pytest</summary>\n\n"
            "```\n"
            "platform linux -- Python 3.12.0, pytest-8.0.0, pluggy-1.5.0\n"
            f"rootdir: {home}/checkout\n"
            "collected 590 items\n"
            "```\n\n</details>\n",
        ),
    )


def clean_pull_request():
    """An ordinary pull request. A guard that fires on this gets switched off."""
    return (
        ("title", "fix(toon): keep decimal form inside the canonical range"),
        (
            "body",
            "## Intent\n\n"
            "`src/hass_axi/toon.py` formats through `Decimal(repr(value))` inside the range.\n\n"
            "```\n"
            "rootdir: /github/workspace\n"
            "collected 590 items\n"
            "```\n\n"
            "Verified against https://homeassistant.example.com with `light.example_lamp`.\n\n"
            "Co-authored-by: Someone <someone@example.org>\n",
        ),
    )


def run_demo():
    """Prove the scanner fails on dirty content without committing any.

    The findings are reported without their values, because this output is
    published: it runs in a public CI log as the first step of the pull request
    check, and the pipeline pastes it into bodies as evidence. The samples are
    leak-shaped by construction -- that is the proof -- so printing them makes
    the demo's own output fail the scan it precedes, which is exactly what the
    pull request that introduced that check did to itself. The exit code and
    the per-rule lines carry the demonstration; the values are the leak.
    """
    fixture = dirty_fixture()
    with tempfile.TemporaryDirectory() as directory:
        for name, content in fixture.items():
            (Path(directory) / name).write_text(content, encoding="utf-8")
        findings = scan_paths(sorted(fixture), root=directory)
    triggered = sorted({finding.rule.name for finding in findings})
    report(findings, scanned=len(fixture), label="synthetic dirty fixture", show_excerpt=False)
    missing = [rule.name for rule in RULES if rule.name not in triggered]
    if missing:
        print(f"error: the demo fixture did not trigger {', '.join(missing)}")
        return 1
    joined = [f for f in findings if f.pass_name == "joined"]
    if not joined:
        print("error: the condensed pass did not fire; a split token would go unnoticed")
        return 1
    # The pull request surface, proven the same way: a scanner that reaches a
    # file but not a pull request reads as a scanner with nothing to report.
    leaky = scan_pull_request(dirty_pull_request())
    fields = sorted({finding.path for finding in leaky})
    if len(fields) != len(PULL_REQUEST_FIELDS):
        print(
            f"error: the pull request scan missed a field; it saw {', '.join(fields) or 'nothing'}"
        )
        return 1
    if any(finding.column is None for finding in leaky):
        print("error: a pull request finding carried no offset to locate it by")
        return 1
    quiet = scan_pull_request(clean_pull_request())
    if quiet:
        print(
            "error: the pull request scan fired on an ordinary pull request: "
            + ", ".join(sorted({finding.rule.name for finding in quiet}))
        )
        return 1
    print(f"demo: every rule fired ({', '.join(triggered)}); the scanner is working")
    print(
        f"demo: a pull request leaking an absolute path was caught in all "
        f"{len(PULL_REQUEST_FIELDS)} fields, and an ordinary one was left alone"
    )
    return 0


def list_rules():
    print(f"rules[{len(RULES)}]{{rule,passes,detects}}:")
    for rule in RULES:
        passes = "line+joined" if rule.condensed else "line"
        print(f"  {rule.name},{passes},{rule.message}")
    print(f"allowances[{len(PATH_ALLOWANCES)}]{{path,rules}}:")
    for candidate, names in sorted(PATH_ALLOWANCES.items()):
        print(f"  {candidate},{'|'.join(sorted(names))}")
    print("surfaces[3]{surface,how}:")
    print("  tracked files,--staged (pre-commit hook) and the whole-tree scan")
    print("  commit message,--commit-msg (commit-msg hook)")
    print("  pull request title and body,--pull-request N (hygiene.yml, on edited too)")
    print("help:")
    print(f"  Exempt one line from one rule with `{ALLOW_PREFIX}<rule>`")
    print("  A file that cannot carry a marker is exempted per rule in PATH_ALLOWANCES")
    print("  Neither exemption applies to a pull request: it is editable after every check")
    return 0


def report(findings, *, scanned, label="tracked files", show_excerpt=True):
    """Print what was found, and where.

    ``show_excerpt=False`` keeps the location and the rule but not the value.
    A hook reading a private checkout wants the excerpt; the self-test does
    not, because its output is published -- a public CI log, and pull request
    bodies it gets pasted into as evidence -- and what it demonstrates with is
    leak-shaped by construction. The pull request reporter made the same
    move first, for the same reason.
    """
    if not findings:
        print(f"leakcheck: 0 findings in {scanned} {label}")
        return
    columns = "file,line,rule,pass,excerpt" if show_excerpt else "file,line,rule,pass"
    print(f"leakcheck[{len(findings)}]{{{columns}}}:")
    for finding in findings:
        line = f"  {finding.path},{finding.line_number},{finding.rule.name},{finding.pass_name}"
        if show_excerpt:
            line += f",{finding.excerpt!r}"
        print(line)
    print("rules:")
    for name in sorted({finding.rule.name for finding in findings}):
        print(f"  {name}: {RULES_BY_NAME[name].message}")
    print("help:")
    print("  Replace the value with a synthetic one: light.example_lamp, area 'Example Room',")
    print("  https://homeassistant.example.com, or read it from the environment instead.")
    print(f"  A line that must keep one shape can carry `{ALLOW_PREFIX}<rule>`.")
    print("  A `joined` finding was assembled across lines; the line shown is where it starts.")
    print("  A `decoded` finding matched a percent-decoded view of the line shown.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="leakcheck",
        description="Scan for installation-specific data that must not enter a public repository.",
    )
    parser.add_argument("paths", nargs="*", help="files or directories to scan")
    parser.add_argument("--staged", action="store_true", help="scan staged content instead")
    parser.add_argument("--commit-msg", metavar="PATH", help="scan a commit message file")
    parser.add_argument(
        "--pull-request",
        metavar="N",
        help="scan a pull request's title and body, read from GitHub",
    )
    parser.add_argument(
        "--repo", metavar="OWNER/NAME", help="repository to ask about (default: origin)"
    )
    parser.add_argument("--demo", action="store_true", help="scan a synthetic dirty tree")
    parser.add_argument("--rules", action="store_true", help="list the rules and exit")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)

    if args.rules:
        return list_rules()
    if args.demo:
        return run_demo()
    if args.pull_request is not None:
        return check_pull_request(args.pull_request, slug=args.repo, root=args.root)
    if args.commit_msg:
        findings = scan_commit_message(args.commit_msg)
        report(findings, scanned=1, label="commit message")
        return 1 if findings else 0
    if args.staged:
        findings, scanned = scan_staged(args.root)
        report(findings, scanned=scanned, label="staged files")
        return 1 if findings else 0

    if args.paths:
        collected = []
        for target in args.paths:
            base = Path(target)
            if not base.is_absolute():
                base = Path(args.root) / target
            for found in walk_files(base):
                try:
                    collected.append(str(found.relative_to(args.root)))
                except ValueError:
                    collected.append(str(found))
        paths = sorted(set(collected))
        label = "files"
    else:
        paths = tracked_files(args.root)
        label = "tracked files"
        if paths is None:
            paths = sorted({str(p) for p in walk_files(args.root)})

    findings = scan_paths(paths, root=args.root)
    report(findings, scanned=len(paths), label=label)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
