#!/usr/bin/env python3
"""Reject a commit message that release-please cannot parse.

Release automation that reports success while shipping nothing is worse than
automation that fails, and that is exactly what happened here: a merged fix sat
on ``main`` unreleased while the release workflow exited 0 with ``Considering: 0
commits``. release-please catches every parse error, logs ``commit could not be
parsed`` at debug level, and carries on with the commits it *could* read -- so a
message it chokes on is silently dropped from the changelog and from the version
bump, and the run stays green.

The rule that bit us
--------------------

release-please 17.3.0 parses commit messages with ``@conventional-commits/parser``
(``^0.4.1``), the reference implementation of the Conventional Commits grammar.
That grammar tries to read **every physical line of the body** as a ``<footer>``::

    <footer> ::= <token>, <separator>, <whitespace>*, <value>
    <token>  ::= <type>, "(" <scope> ")", ["!"] | <type>
    <type>   ::= 1*<any UTF8-octet except newline, parens, "!", ":" or whitespace>
    <scope>  ::= 1*<any UTF8-octet except newline or parens>

``<type>`` swallows characters from the start of the line until it meets one of
whitespace, a newline, ``!``, ``:``, ``(`` or ``)``. If the character it stops on
is ``(``, the parser **commits** to reading a scope: it consumes up to the next
``(``, ``)`` or newline, and if that character is not ``)`` it *throws*::

    if (scanner.peek() !== ')') {
      throw scanner.abort(node, [')'])          // lib/parser.js:177
    }

That ``throw`` is one of only four in the whole grammar, and it is the only one
reachable from the body. Every other production reports failure by *returning* an
``Error`` its caller can back out of; this one unwinds the entire parse. So a line
like::

    `Decimal(repr(value))` inside the canonical range - the shortest round-tripping

is unparseable, while the identical phrase one word further along the line is
fine. It is not parentheses, not backticks, not the dash and not the position on
its own -- it is the interaction: **a body line whose first character begins an
unbroken run of non-space, non-parenthesis, non-``!``, non-``:`` characters that
is immediately followed by ``(``, where the parenthesis does not close before the
next ``(`` or the end of that line.**

Rich commit bodies are not the problem and are not what this guard restricts. A
line reflowed by one word, or a space before the parenthesis, satisfies the
grammar with the prose intact.

The message is not always the message
-------------------------------------

The rule above was diagnosed correctly and the guard built on it still passed a
commit release-please dropped, because it was reading the wrong string.
release-please parses ``splitMessages(preprocessCommitMessage(commit))``, and
``preprocessCommitMessage`` is this::

    const overrideMessage = (commit.pullRequest.body.split('BEGIN_COMMIT_OVERRIDE')[1] || '')
      .split('END_COMMIT_OVERRIDE')[0]
      .trim()
    if (overrideMessage) return overrideMessage

``String.split`` finds that literal **anywhere in the pull request body**,
including inside a sentence that merely names it. So a pull request whose body
*explained* this mechanism handed release-please the paragraph that followed the
word, as the commit message. It began ``block from the PR body when there is
one``, ``block`` is five characters, and the parser stopped on the space after
it: ``unexpected token ' ' at 1:6``. The commit message itself parsed perfectly,
which is why a checker that only ever saw the commit message reported success.

Three artefacts reach release-please and only one of them passes under a
commit-msg hook:

============================  ==========================  =====================
artefact                      written                     checked by
============================  ==========================  =====================
the commit message            locally, by a developer     ``--commit-msg``
the merge commit's message    in GitHub's merge box       ``--since-release``
the pull request body         in GitHub's editor          ``--pull-request``
============================  ==========================  =====================

The body is the dangerous one: it *replaces* the other two, it can be edited
after every check has run, and nothing in the repository records it. So the
audit modes resolve it from GitHub rather than trusting git, and refuse to
report a verdict they cannot support.

One rule here is stricter than upstream, deliberately. Upstream is happy with an
override block that is never closed -- it reads to the end of the body -- and
that is exactly the shape an accidental mention takes. A body that names the
marker must close the block, so that an accidental mention whose next paragraph
*happens* to parse cannot silently become the changelog entry.

Two engines, one answer
-----------------------

``--engine node`` runs the real parser: ``vendor/conventional-commits-parser/``
holds a byte-for-byte copy of the four dependency-free modules that make it up,
so ``node`` alone runs it with no ``npm install`` and no network. That is the
authoritative answer, because it is upstream's own code rather than a description
of it.

``--engine python`` runs the transcription in this file -- a line-by-line port of
the same grammar -- so a machine with no ``node`` still gets a verdict rather than
a skip. ``tests/test_commit_message.py`` runs the whole corpus through both and
asserts they agree on the verdict *and* on the reported line, column and token; a
guard whose fallback disagreed with the thing it stands in for would be worse than
none.

``--engine auto`` (the default, and what the hooks use) prefers ``node``.

Usage:
  scripts/commitcheck.py --commit-msg PATH    check one message file (the hook)
  scripts/commitcheck.py --message TEXT       check a literal message
  scripts/commitcheck.py --commit SHA         check one commit as release-please reads it
  scripts/commitcheck.py --pull-request N     check what a merge will hand release-please
  scripts/commitcheck.py --range A..B         check every commit message in a range
  scripts/commitcheck.py --since-release      check every commit since the released tag
  scripts/commitcheck.py --rules              explain the rule, the engines and the allowances
  scripts/commitcheck.py --demo               self-test: prove it still rejects and still accepts

The modes that name a commit or a pull request read pull request bodies from
GitHub, with a token from ``GITHUB_TOKEN``, ``GH_TOKEN`` or ``gh auth token``.
``--pull-requests require`` (the workflows) fails without one rather than
checking a different artefact and calling it green.

Exit status is 0 when every message parses and 1 when one does not, so the hooks
and the release workflow can both use it directly.

Standard library only, so the hooks run without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The vendored copy of the parser release-please runs. See its PROVENANCE.md.
VENDORED_PARSER = REPO_ROOT / "vendor" / "conventional-commits-parser" / "lib" / "parser.js"

#: ``throw`` statements in the vendored ``lib/parser.js``. Asserted by the suite:
#: a refreshed grammar that grew or lost one has changed where a message can be
#: rejected, and the transcription below has to be re-read rather than trusted.
THROW_SITES = 4

#: Commits that are known to be unparseable, already accounted for, and must not
#: re-fail the release audit for ever. Scoped exactly like ``PATH_ALLOWANCES`` in
#: ``leakcheck.py``: one full SHA, one reason, printed by ``--rules``, and pinned
#: by the suite so an entry that has outlived its cause fails rather than quietly
#: covering something new.
#:
#: Empty here, and that is a measured fact rather than an assumption: every
#: commit in this repository's history was run through the parser and all of them
#: parse. One of them parses by a single word -- see
#: ``tests/fixtures/commit-messages/`` -- which is why this guard is here despite
#: nothing having been lost yet.
KNOWN_UNPARSEABLE = {}


# ---------------------------------------------------------------------------
# The transcription.
#
# A port of `@conventional-commits/parser@0.4.1` `lib/parser.js`, `lib/scanner.js`,
# `lib/type-checks.js` and `lib/codes.js`, kept structurally identical to the
# original so the two can be read side by side. Upstream reports failure two
# ways and the difference is the whole point of this file: `scanner.abort`
# *returns* an `Error` the caller may ignore, and four sites `throw` one. Here,
# `_abort` returns `_Rejected` and the same four sites raise `CommitParseError`.
# ---------------------------------------------------------------------------

CR = "\u000d"
LF = "\u000a"
ZWNBSP = "\ufeff"
TAB = "\u0009"
VT = "\u000b"
FF = "\u000c"
SP = "\u0020"
NBSP = "\u00a0"

_WHITESPACE = frozenset({ZWNBSP, TAB, VT, FF, SP, NBSP})

#: What JavaScript's `String.prototype.trim` removes, which is not what Python's
#: `str.strip` removes: JS counts ZWNBSP and the line separators, Python does not
#: count ZWNBSP. `message()` trims before it scans, so the difference moves every
#: reported line number when a message happens to start with one.
_JS_TRIM = frozenset(
    {CR, LF, ZWNBSP, TAB, VT, FF, SP, NBSP, "\u2028", "\u2029"}
    | {chr(c) for c in range(0x11000) if unicodedata.category(chr(c)) == "Zs"}
)


def _is_whitespace(token):
    return token in _WHITESPACE


def _is_newline(token):
    return bool(token) and token[0] in (CR, LF)


def _is_parens(token):
    return token in ("(", ")")


class CommitParseError(Exception):
    """A message the grammar refuses -- what release-please silently drops.

    Carries the position and tokens upstream reports, so the two engines can be
    compared field by field rather than by string equality on an error message.
    """

    def __init__(self, message, line, column, found, valid):
        super().__init__(message)
        self.line = line
        self.column = column
        self.found = found  # the offending token, or None at end of input
        self.valid = valid  # what the grammar would have accepted there


class _Rejected:
    """A production that did not match. Upstream's ``Error`` return value."""

    def __init__(self, error):
        self.error = error


class _Node:
    __slots__ = ("start", "type", "value")

    def __init__(self, node_type, start):
        self.type = node_type
        self.value = ""
        self.start = start


class _Scanner:
    def __init__(self, text):
        self.text = text
        self.line = 1
        self.column = 1
        self.offset = 0

    def eof(self):
        return self.offset >= len(self.text)

    def next(self, n=None):
        token = self.text[self.offset : self.offset + n] if n else self.peek()
        self.offset += len(token)
        self.column += len(token)
        if _is_newline(token):
            self.line += 1
            self.column = 1
        return token

    def peek(self):
        token = self.text[self.offset : self.offset + 1]
        if token == CR and self.text[self.offset + 1 : self.offset + 2] == LF:
            token += LF
        return token

    def peek_literal(self, literal):
        return self.text[self.offset : self.offset + len(literal)] == literal

    def position(self):
        return (self.line, self.column, self.offset)

    def rewind(self, position):
        self.line, self.column, self.offset = position

    def enter(self, node_type):
        return _Node(node_type, self.position())

    def abort(self, node, expected=None):
        """Upstream ``Scanner.abort``: build the error, then rewind."""
        line, column, _ = self.position()
        valid = [t for t in expected if t] if expected is not None else [f"<{node.type}>"]
        joined = ", ".join(valid)
        if self.eof():
            found = None
            text = f"unexpected token EOF at {line}:{column}, valid tokens [{joined}]"
        else:
            found = self.peek()
            text = f"unexpected token '{found}' at {line}:{column}, valid tokens [{joined}]"
        self.rewind(node.start)
        return _Rejected(CommitParseError(text, line, column, found, valid))


def _message(commit_text):
    """``<summary>`` then an optional body and footers. Upstream ``message``.

    Returns nothing where upstream returns a syntax tree: the only question here
    is whether the parse survives, so the tree would be built and thrown away.
    Every production below still runs in the order and with the backtracking
    upstream uses, because *where* it gives up is what decides that.
    """
    scanner = _Scanner(_js_trim(commit_text))

    result = _summary(scanner)
    if isinstance(result, _Rejected):
        raise result.error  # lib/parser.js:17
    if scanner.eof():
        return

    nl = _newline(scanner)
    if isinstance(nl, _Rejected):
        raise nl.error  # lib/parser.js:30
    body = _body(scanner)
    if isinstance(body, _Rejected):
        body = None
    if scanner.eof():
        return

    if body is not None:
        nl = _newline(scanner)
        if isinstance(nl, _Rejected):
            raise nl.error  # lib/parser.js:48

    while not scanner.eof():
        if isinstance(_footer(scanner), _Rejected):
            break
        if isinstance(_newline(scanner), _Rejected):
            break


def _summary(scanner):
    """``<type> ["(" <scope> ")"] ["!"] ":" <whitespace>* <text>``."""
    node = scanner.enter("summary")

    result = _type(scanner)
    if isinstance(result, _Rejected):
        return result

    scope = _scope(scanner)
    if isinstance(scope, _Rejected):
        scope = None

    bang = _breaking_change(scanner)
    if isinstance(bang, _Rejected):
        bang = None

    if isinstance(_separator(scanner), _Rejected):
        return scanner.abort(node, [None if scope else "(", None if bang else "!", ":"])

    _whitespace(scanner)
    _text(scanner)
    return node


def _type(scanner):
    """``1*<any octet except newline, parens, "!", ":" or whitespace>``."""
    node = scanner.enter("type")
    while not scanner.eof():
        token = scanner.peek()
        if _is_parens(token) or _is_whitespace(token) or _is_newline(token) or token in ("!", ":"):
            break
        node.value += scanner.next()
    if node.value == "":
        return scanner.abort(node)
    return node


def _text(scanner):
    node = scanner.enter("text")
    while not scanner.eof():
        if _is_newline(scanner.peek()):
            break
        node.value += scanner.next()
    return node


def _scope(scanner):
    """``"(" 1*<any octet except newline or parens> ")"``.

    The one production in the grammar that raises rather than returns. Once the
    opening parenthesis is consumed the parser is committed: anything but ``)``
    where the closing parenthesis belongs unwinds the whole parse, and that is
    the failure this script exists to catch before it reaches ``main``.
    """
    if scanner.peek() != "(":
        return scanner.abort(scanner.enter("scope"))
    scanner.next()

    node = scanner.enter("scope")
    while not scanner.eof():
        token = scanner.peek()
        if _is_parens(token) or _is_newline(token):
            break
        node.value += scanner.next()

    if scanner.peek() != ")":
        raise scanner.abort(node, [")"]).error  # lib/parser.js:177
    scanner.next()

    if node.value == "":
        return scanner.abort(node)
    return node


def _body(scanner):
    """Every body line, each first offered to ``<footer>``. Upstream ``body``."""
    node = scanner.enter("body")

    if not isinstance(_pre_footer(scanner), _Rejected):
        return scanner.abort(node)

    breaking = _breaking_change(scanner, allow_bang=False)
    if not isinstance(breaking, _Rejected) and scanner.peek() == ":":
        _separator(scanner)
        _whitespace(scanner)

    _text(scanner)
    nl = _newline(scanner)
    if not isinstance(nl, _Rejected) and isinstance(_body(scanner), _Rejected):
        scanner.abort(nl)
    return node


def _pre_footer(scanner):
    node = scanner.enter("pre-footer")
    while not scanner.eof():
        _newline(scanner)
        if isinstance(_footer(scanner), _Rejected):
            return scanner.abort(node)
    return node


def _footer(scanner):
    """``<token> <separator> <whitespace>* <value>``."""
    node = scanner.enter("footer")

    result = _token(scanner)
    if isinstance(result, _Rejected):
        return result

    separator = _separator(scanner)
    if isinstance(separator, _Rejected):
        scanner.abort(node)
        return separator

    _whitespace(scanner)
    _value(scanner)
    return node


def _token(scanner):
    """``<breaking-change> | <type> ["(" <scope> ")"] ["!"]``.

    The route from an ordinary body line into :func:`_scope`.
    """
    node = scanner.enter("token")

    breaking = _breaking_change(scanner)
    if isinstance(breaking, _Rejected):
        scanner.abort(node)
    else:
        return node

    result = _type(scanner)
    if isinstance(result, _Rejected):
        return result
    _scope(scanner)
    _breaking_change(scanner)
    return node


def _breaking_change(scanner, allow_bang=True):
    """``"!" | "BREAKING CHANGE" | "BREAKING-CHANGE"``."""
    node = scanner.enter("breaking-change")
    if scanner.peek() == "!" and allow_bang:
        node.value = scanner.next()
    elif scanner.peek_literal("BREAKING CHANGE") or scanner.peek_literal("BREAKING-CHANGE"):
        node.value = scanner.next(len("BREAKING CHANGE"))
    if node.value == "":
        return scanner.abort(node, ["BREAKING CHANGE"])
    return node


def _value(scanner):
    node = scanner.enter("value")
    _text(scanner)
    while not isinstance(_continuation(scanner), _Rejected):
        pass
    return node


def _continuation(scanner):
    node = scanner.enter("continuation")
    nl = _newline(scanner)
    if isinstance(nl, _Rejected):
        return nl
    whitespace = _whitespace(scanner)
    if isinstance(whitespace, _Rejected):
        scanner.abort(node)
        return whitespace
    _text(scanner)
    return node


def _separator(scanner):
    """``":" | " #"``."""
    node = scanner.enter("separator")
    if scanner.peek() == ":":
        node.value = scanner.next()
        return node
    if scanner.peek() == " ":
        scanner.next()
        if scanner.peek() == "#":
            scanner.next()
            node.value = " #"
            return node
        return scanner.abort(node)
    return scanner.abort(node)


def _whitespace(scanner):
    node = scanner.enter("whitespace")
    while _is_whitespace(scanner.peek()):
        node.value += scanner.next()
    if node.value == "":
        return scanner.abort(node, [" "])
    return node


def _newline(scanner):
    node = scanner.enter("newline")
    while _is_newline(scanner.peek()):
        node.value += scanner.next()
    if node.value == "":
        return scanner.abort(node, ["<CR><LF>", "<LF>"])
    return node


#: The same set as a string, because `str.strip` takes characters, not a set.
_JS_TRIM_CHARS = "".join(sorted(_JS_TRIM))


def _js_trim(text):
    return text.strip(_JS_TRIM_CHARS)


# ---------------------------------------------------------------------------
# release-please's own preprocessing, and the two engines.
# ---------------------------------------------------------------------------

#: The marker release-please looks for in a **pull request body**, from
#: `preprocessCommitMessage` in the same file. Its presence throws the commit
#: message away: everything after the first occurrence, up to the first closing
#: marker, becomes the message that is parsed. `String.split` finds the literal
#: anywhere, including inside prose that merely names it, which is how a pull
#: request *describing* this mechanism came to trigger it.
OVERRIDE_START = "BEGIN_COMMIT_OVERRIDE"
OVERRIDE_END = "END_COMMIT_OVERRIDE"


def override_block(pull_request_body):
    """The text release-please would parse *instead of* the commit message.

    Transcribed from release-please 17.3.0 `build/src/commit.js`::

        const overrideMessage = (commit.pullRequest.body.split('BEGIN_COMMIT_OVERRIDE')[1] || '')
          .split('END_COMMIT_OVERRIDE')[0]
          .trim()
        if (overrideMessage) return overrideMessage

    ``None`` when there is no override, which is also what an *empty* block
    means upstream: an empty string is falsy there, so the commit message is used
    after all.
    """
    if not pull_request_body or OVERRIDE_START not in pull_request_body:
        return None
    block = _js_trim(pull_request_body.split(OVERRIDE_START)[1].split(OVERRIDE_END)[0])
    return block or None


def override_is_closed(pull_request_body):
    """Whether the block that starts the override is ever closed."""
    if not pull_request_body or OVERRIDE_START not in pull_request_body:
        return True
    return OVERRIDE_END in pull_request_body.split(OVERRIDE_START)[1]


def preprocess_commit_message(message, pull_request_body=None):
    """What release-please actually hands its parser for this commit.

    The whole reason this function exists: release-please parses
    ``splitMessages(preprocessCommitMessage(commit))``, and a check that only
    ever saw ``message`` is validating a string release-please never reads.
    """
    block = override_block(pull_request_body)
    return message if block is None else block


#: The types release-please 17.3.0 splits a single message on, from
#: `build/src/commit.js` `splitMessages`. One commit can carry several
#: conventional commits, and each is parsed -- and can fail -- on its own.
_SPLIT = re.compile(
    r"\r?\n\r?\n"
    r"(?=(?:feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(?:\(.*?\))?: )"
)


def split_messages(message):
    """Transcription of release-please's ``splitMessages``.

    A commit body may hold further conventional commits, either after a blank
    line or inside ``BEGIN_NESTED_COMMIT``/``END_NESTED_COMMIT``. Each part is
    parsed separately, so one unparseable part loses only that part -- which is
    still a silent loss, and still a rejection here.
    """
    parts = message.split("BEGIN_NESTED_COMMIT")
    messages = [parts[0]]
    for part in parts[1:]:
        nested, *rest = part.split("END_NESTED_COMMIT")
        messages.append(nested)
        messages[0] = messages[0] + "END_NESTED_COMMIT".join(rest)
    conventional = [part for part in _SPLIT.split(messages[0]) if part]
    return conventional + messages[1:]


#: Upstream's error string, taken apart so the two engines can be compared field
#: by field. DOTALL is load-bearing: the offending token is very often the
#: newline the scope ran into, and a pattern that could not match one would round
#: every such rejection off to "an error shape this script has never seen".
_ERROR = re.compile(
    r"\Aunexpected token (?:'(?P<found>.*?)'|(?P<eof>EOF)) at (?P<line>\d+):(?P<column>\d+), "
    r"valid tokens \[(?P<valid>.*)\]\Z",
    re.DOTALL,
)

_NODE_RUNNER = """
const parser = require(process.env.COMMITCHECK_PARSER);
let data = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { data += chunk; });
process.stdin.on('end', () => {
  try {
    parser(data);
    process.stdout.write(JSON.stringify({ok: true}));
  } catch (err) {
    process.stdout.write(JSON.stringify({ok: false, message: err.message}));
  }
});
"""


def node_available():
    """Whether ``--engine node`` can run: an interpreter and the vendored copy."""
    if not VENDORED_PARSER.exists():
        return False
    try:
        subprocess.run(
            ["node", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def resolve_engine(requested):
    """``auto`` prefers the real parser and falls back to the transcription."""
    if requested != "auto":
        return requested
    return "node" if node_available() else "python"


def _parse_python(part):
    try:
        _message(part)
    except CommitParseError as error:
        return error
    return None


def _parse_node(part):
    environment = dict(os.environ, COMMITCHECK_PARSER=str(VENDORED_PARSER))
    result = subprocess.run(
        ["node", "-e", _NODE_RUNNER],
        input=part,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"the vendored parser could not be run: {result.stderr.strip()}")
    verdict = json.loads(result.stdout)
    if verdict["ok"]:
        return None
    text = verdict["message"]
    match = _ERROR.match(text)
    if not match:
        # An error shape this script has never seen. Report it rather than
        # rounding it off to a pass -- an unrecognised refusal is still one.
        return CommitParseError(text, 0, 0, None, [])
    return CommitParseError(
        text,
        int(match["line"]),
        int(match["column"]),
        None if match["eof"] else match["found"],
        [t for t in match["valid"].split(", ") if t],
    )


def parse_part(part, engine="python"):
    """Parse one conventional-commit part. ``None`` when it parses."""
    return _parse_node(part) if engine == "node" else _parse_python(part)


# ---------------------------------------------------------------------------
# The verdict a caller acts on.
# ---------------------------------------------------------------------------


#: What a problem was found in. Not decoration: the three are written in
#: different places, by different people, at different times, and "fix the commit
#: message" is useless advice to somebody whose commit message is fine and whose
#: pull request body is what release-please read.
COMMIT_MESSAGE = "the commit message"
OVERRIDE_ARTEFACT = "the commit-override block in the pull request body"
TITLE_ARTEFACT = "the subject GitHub composes from the pull request title"


class Problem:
    """One unparseable part of one artefact, located in the text as written."""

    def __init__(
        self, error, *, part_index, part_count, line, source_line, artefact=COMMIT_MESSAGE
    ):
        self.error = error
        self.part_index = part_index
        self.part_count = part_count
        self.line = line  # absolute line in the text the author wrote
        self.source_line = source_line
        self.artefact = artefact

    @property
    def column(self):
        return self.error.column

    def advice(self):
        """What to change. Named for the shape, not for the parser's vocabulary."""
        lines = []
        if self.artefact == OVERRIDE_ARTEFACT:
            lines = [
                "This is NOT the commit message. The pull request body contains release-please's",
                "commit-override marker, so it threw the message away and parsed the text after",
                "the marker instead -- the position above is a line of that text.",
                "  * if you did not mean to override anything, do not spell the marker out in a",
                "    pull request body; describe it instead. Naming it is enough to trigger it.",
                "  * if you did, make the block a conventional commit and close it with the",
                "    matching END marker.",
                "",
            ]
        elif self.artefact == TITLE_ARTEFACT:
            lines = [
                "This is the pull request title, which GitHub offers as the squash subject.",
                "Retitle the pull request rather than relying on someone editing the merge box.",
                "",
            ]
        if ")" in self.error.valid:
            return [
                *lines,
                "A body line that begins with text followed straight away by '(' is read as a",
                "conventional-commit footer with a scope, and that scope must close with ')'",
                "before the next '(' or the end of the line.",
                "Fix it by keeping the prose and moving the parenthesis off the line start:",
                "  * reflow so the line does not begin with the parenthesised term; or",
                "  * put a word or a space before it -- `foo(bar(baz))` is fine mid-line; or",
                "  * close the parentheses on the same line, without nesting.",
            ]
        if "<CR><LF>" in self.error.valid or "<LF>" in self.error.valid:
            return [
                *lines,
                "The subject line has to be followed by a blank line before any body.",
            ]
        return [
            *lines,
            "The first line has to be a conventional-commit summary: 'type: subject', or",
            "'type(scope): subject', optionally with '!' before the colon for a breaking",
            "change. release-please skips anything else without saying so.",
        ]


def override_faults(pull_request_body):
    """Faults in the override *block itself*, before anything is parsed.

    Upstream is happy with a block that is never closed -- it simply reads to the
    end of the body -- and that is precisely the shape an accidental mention
    takes, so this repository is stricter than upstream by one rule: a body that
    names the marker must close the block. Without that rule an accidental
    mention whose following paragraph *happens* to parse would silently become
    the commit message and the changelog entry, and nothing would say so.

    Returned as lines rather than as ``Problem``s because no parser was involved:
    inventing a line and column for it would dress a policy up as a parse error.
    """
    block = override_block(pull_request_body)
    if block is None or override_is_closed(pull_request_body):
        return []
    return [
        "The pull request body names release-please's commit-override marker and never",
        "closes the block, so everything after the marker becomes the commit message:",
        f"    {block.splitlines()[0][:76]}",
        "If that was prose about the mechanism rather than an override, describe the",
        "marker instead of spelling it out. If it was deliberate, close it with the",
        "matching END marker so the intent is visible.",
    ]


def check_pull_request(title, number, body, engine="python"):
    """What release-please will read once this pull request is merged.

    Two artefacts, and neither of them is the message the commit-msg hook saw:

    * the override block in the body, which replaces whatever anyone types into
      the merge box, and
    * the subject GitHub composes from the title, which is what lands unless
      somebody types something else there.

    The merge box itself cannot be checked before it is used, which is what the
    post-merge audit is for. These two can, and the one that has actually cost a
    release is the first.
    """
    faults = override_faults(body)
    problems = []
    block = override_block(body)
    if block is not None:
        problems.extend(check_text(block, engine=engine, artefact=OVERRIDE_ARTEFACT))
    subject = f"{title} (#{number})" if number else title
    problems.extend(check_text(subject, engine=engine, artefact=TITLE_ARTEFACT))
    return problems, faults


def check(message, engine="python", pull_request_body=None):
    """Every problem release-please would hit with this commit.

    ``pull_request_body`` is not decoration. release-please parses
    ``splitMessages(preprocessCommitMessage(commit))``, and the preprocess step
    replaces the whole message with the pull request's override block whenever
    the body carries the marker -- so with the body left out this answers a
    question about a string release-please may never look at. That is exactly how
    the guard that shipped in 0.2.2's follow-up passed a commit release-please
    dropped: the message was fine, and the message was not what it read.
    """
    block = override_block(pull_request_body)
    if block is None:
        return check_text(message, engine=engine)
    return check_text(block, engine=engine, artefact=OVERRIDE_ARTEFACT)


def check_text(message, engine="python", artefact=COMMIT_MESSAGE):
    """Every problem release-please would hit in one already-resolved text."""
    problems = []
    parts = split_messages(message)
    lines = message.splitlines()
    cursor = 0
    for index, part in enumerate(parts):
        error = parse_part(part, engine=engine)
        offset = message.find(part, cursor)
        if offset >= 0:
            cursor = offset + len(part)
        if error is None:
            continue
        line = _absolute_line(message, part, offset, error.line)
        source = lines[line - 1] if 0 < line <= len(lines) else ""
        problems.append(
            Problem(
                error,
                part_index=index,
                part_count=len(parts),
                line=line,
                source_line=source,
                artefact=artefact,
            )
        )
    return problems


def _absolute_line(message, part, offset, parsed_line):
    """Map a line number the parser reported back onto the message as written.

    The parser numbers lines within the *trimmed* part it was handed, so both the
    split and the trim have to be undone before the number means anything to the
    person who has to fix the line. A part that cannot be located (only reachable
    through the ``BEGIN_NESTED_COMMIT`` concatenation, which rebuilds part 0 from
    pieces) keeps the part-relative number rather than inventing one.
    """
    if offset < 0:
        return parsed_line
    leading = len(part) - len(part.lstrip(_JS_TRIM_CHARS))
    return message[: offset + leading].count("\n") + parsed_line


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------


def report(problems, label, stream=None):
    """Print every problem with the offending line under a caret, and the fix.

    ``stream`` is resolved at call time rather than bound as a default: a default
    argument captures whatever ``sys.stderr`` was when this module was imported,
    which is not the stream a caller redirected afterwards.
    """
    stream = sys.stderr if stream is None else stream
    print(f"commitcheck: {label} cannot be parsed by release-please", file=stream)
    for problem in problems:
        where = f"in {problem.artefact}, line {problem.line}"
        if problem.column:
            where += f", column {problem.column}"
        if problem.part_count > 1:
            where += f" (conventional-commit section {problem.part_index + 1})"
        print(f"  {where}: {problem.error}", file=stream)
        if problem.source_line:
            print(f"    {problem.source_line}", file=stream)
            print(f"    {' ' * max(problem.column - 1, 0)}^", file=stream)
        for line in problem.advice():
            print(f"  {line}", file=stream)
    print(
        "  release-please would log 'commit could not be parsed', drop this commit\n"
        "  from the changelog and the version bump, and still exit 0.",
        file=stream,
    )
    return 1


# ---------------------------------------------------------------------------
# Modes.
# ---------------------------------------------------------------------------


def released_version(root):
    manifest = Path(root) / ".release-please-manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["."]


def _tag_exists(tag, root):
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def release_range(root=".", stream=None):
    """The range to audit, and the tag it starts from.

    The manifest names the last *released* version, so ``v<version>..HEAD`` is
    the set release-please is about to read. On the run that publishes a release
    the manifest has already been bumped while the matching tag may not exist
    yet -- the audit and the release job run at the same time -- so a missing tag
    falls back to the newest release tag that does exist rather than failing on a
    race. Either way the audit covers a superset of what is still unreleased, and
    it says which tag it used.
    """
    stream = sys.stdout if stream is None else stream
    tag = f"v{released_version(root)}"
    if _tag_exists(tag, root):
        return f"{tag}..HEAD"
    existing = subprocess.run(
        ["git", "-C", str(root), "tag", "--list", "v*", "--sort=-v:refname"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for candidate in existing:
        if _tag_exists(candidate, root):
            print(
                f"commitcheck: {tag} does not exist yet; auditing from {candidate} instead",
                file=stream,
            )
            return f"{candidate}..HEAD"
    print("commitcheck: no release tag exists yet; auditing the whole history", file=stream)
    return "HEAD"


def commits_since(rev_range, root="."):
    """``[(sha, message)]`` for a revision range, newest first.

    ``--first-parent`` because that is the set release-please reads: it asks
    GitHub for the *merge commits on the branch*, not for everything reachable
    from it. Without it a work-in-progress message inside a merged branch would
    be reported as a commit release-please dropped, when release-please was never
    going to look at it -- a guard that cries wolf gets switched off.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--first-parent", "-z", "--format=%H%n%B", rev_range],
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for record in result.stdout.split("\0"):
        if not record.strip():
            continue
        sha, _, body = record.partition("\n")
        commits.append((sha.strip(), body))
    return commits


# ---------------------------------------------------------------------------
# The half of the artefact that is not in git.
#
# A commit message lives in the repository; the pull request body that can
# replace it lives only on GitHub. Standard library only, like the rest of this
# file, so the hooks and the workflows run it the same way.
# ---------------------------------------------------------------------------


class GitHubUnavailable(Exception):
    """No credential, or no network. Never a verdict."""


class GitHubMissing(GitHubUnavailable):
    """GitHub does not have this object. That *is* a verdict, once qualified.

    A commit GitHub cannot resolve -- one that has not been pushed yet -- has no
    pull request, and therefore nothing that could replace its message. Treating
    that as an outage would fail every local run with unpushed work; treating a
    404 as "no pull request" without qualification would turn a bad credential
    into a silent all-clear. So ``resolve_bodies`` proves the repository itself
    is reachable first, and only then reads a per-commit miss as an answer.
    """


def github_token():
    """A token from the environment, or from ``gh`` if the operator has one."""
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def repo_slug(root="."):
    """``owner/name``, from the environment CI sets or from the origin remote."""
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if slug:
        return slug
    result = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    url = result.stdout.strip().removesuffix(".git")
    match = re.search(r"[:/]([^/:]+/[^/]+)$", url)
    return match.group(1) if match else None


def github_json(path, *, token, api_url=None):
    """One GET against the REST API, or ``GitHubUnavailable``."""
    base = api_url or os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "commitcheck",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 422 is what the commit endpoints answer for a SHA GitHub does not have,
        # which is an ordinary state locally and never one on a pushed branch.
        if exc.code in (404, 422):
            raise GitHubMissing(f"GET {path}: {exc}") from exc
        raise GitHubUnavailable(f"GET {path}: {exc}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubUnavailable(f"GET {path}: {exc}") from exc


def pull_request(number, *, slug, token, api_url=None):
    return github_json(f"/repos/{slug}/pulls/{number}", token=token, api_url=api_url)


def pull_request_body_for_commit(sha, *, slug, token, api_url=None):
    """The body of the pull request release-please would associate with ``sha``.

    release-please prefers the pull request whose merge commit *is* this commit
    and falls back to the first one associated with it; the REST endpoint lists
    the same associations the GraphQL query walks. ``None`` means no pull request,
    which is a real answer -- a commit pushed straight to the branch has no body
    to override it.
    """
    pulls = github_json(f"/repos/{slug}/commits/{sha}/pulls", token=token, api_url=api_url)
    if not pulls:
        return None
    merged = next((pr for pr in pulls if pr.get("merge_commit_sha") == sha), None)
    return (merged or pulls[0]).get("body")


def resolve_bodies(commits, *, mode, root=".", slug=None, stream=None):
    """``{sha: pull request body}`` for the commits release-please will read.

    ``mode`` is the difference between an audit that is honest about its reach and
    one that is not:

    * ``require`` -- the workflows. No credential, or one failed lookup, and the
      audit fails rather than checking a different artefact and calling it green.
    * ``auto`` -- a developer's checkout. Consults GitHub when it can and says
      plainly, in the output, when it could not.
    * ``skip`` -- git only, on purpose.

    There is no fourth mode where it quietly falls back, because that is the
    state the previous guard shipped in.
    """
    stream = sys.stdout if stream is None else stream
    if mode == "skip":
        return {}, ["pull request bodies: not consulted (--pull-requests skip)"]
    token = github_token()
    slug = slug or repo_slug(root)
    if not token or not slug:
        missing = "no GitHub token" if not token else "no owner/name for this repository"
        if mode == "require":
            raise GitHubUnavailable(missing)
        return {}, [
            f"pull request bodies: NOT consulted ({missing}).",
            "  release-please replaces a commit message with the override block in its pull",
            "  request body, so this run checked git's copy and not necessarily the text",
            "  release-please will read. Export GITHUB_TOKEN for the whole answer.",
        ]
    # Reach the repository once before reading anything into a per-commit miss.
    # Without this, a bad credential would answer 404 for every commit and the
    # audit would report "no pull request" for all of them -- which is exactly
    # the blind spot it exists to close, arrived at from the other direction.
    try:
        github_json(f"/repos/{slug}", token=token)
    except GitHubUnavailable as exc:
        if mode == "require":
            raise
        return {}, [
            f"pull request bodies: NOT consulted ({exc}).",
            "  This run checked git's copy of the messages and not necessarily the text",
            "  release-please will read.",
        ]
    bodies = {}
    unknown = []
    for sha, _ in commits:
        try:
            bodies[sha] = pull_request_body_for_commit(sha, slug=slug, token=token)
        except GitHubMissing:
            # Not pushed. release-please cannot see it either, in this form.
            unknown.append(sha)
        except GitHubUnavailable:
            if mode == "require":
                raise
            return bodies, [
                f"pull request bodies: INCOMPLETE -- {slug} stopped answering at {sha[:12]}.",
                "  Treat the verdict below as covering git's copy of the messages only.",
            ]
    overridden = sum(1 for sha in bodies if override_block(bodies[sha]) is not None)
    note = f"pull request bodies: consulted for {len(bodies)} commit(s) via {slug}"
    if overridden:
        note += f"; {overridden} carry a commit-override block"
    notes = [note]
    if unknown:
        notes.append(
            f"  {len(unknown)} commit(s) are not on {slug} yet, so they have no pull request "
            "to read: " + ", ".join(sha[:12] for sha in unknown)
        )
    return bodies, notes


def audit_range(rev_range, *, engine, root=".", stream=None, pull_requests="auto", slug=None):
    """Fail when release-please would silently drop a commit from ``rev_range``.

    The pathology this exists for is a *green* release run that considered zero
    commits while commits were waiting, so the count is printed either way. The
    healthy no-op -- nothing since the tag -- and the broken one now read
    differently instead of both reading as success.

    The other green failure is quieter still: a body whose unclosed override
    block *parses*, so every count here reads healthy while the changelog entry
    is an accidental paragraph. ``check`` cannot see that -- the parse is fine --
    which is why the body is faulted wherever it is read, here as on the pull
    request.
    """
    stream = sys.stdout if stream is None else stream
    commits = commits_since(rev_range, root=root)
    bodies, notes = resolve_bodies(commits, mode=pull_requests, root=root, slug=slug, stream=stream)
    dropped = []
    allowed = []
    hijacked = []
    for sha, message in commits:
        body = bodies.get(sha)
        problems = check(message, engine=engine, pull_request_body=body)
        faults = override_faults(body)
        if faults:
            # KNOWN_UNPARSEABLE says a lost *message* was accounted for
            # elsewhere; it never granted a body permission to replace one.
            hijacked.append((sha, message, faults))
        if not problems:
            continue
        if sha in KNOWN_UNPARSEABLE:
            allowed.append(sha)
            continue
        dropped.append((sha, message, problems))

    considered = len(commits) - len(dropped) - len(allowed)
    print(f"commitcheck: {rev_range} -- {len(commits)} commit(s)", file=stream)
    for note in notes:
        print(f"  {note}", file=stream)
    print(f"  release-please would consider: {considered}", file=stream)
    print(f"  silently dropped:              {len(dropped)}", file=stream)
    if hijacked:
        print(f"  messages replaced by an unclosed override block: {len(hijacked)}", file=stream)
    if allowed:
        print(f"  known-unparseable, accounted for: {len(allowed)}", file=stream)
        for sha in allowed:
            print(f"    {sha[:12]} {KNOWN_UNPARSEABLE[sha].splitlines()[0]}", file=stream)

    if not dropped and not hijacked:
        if commits and considered == 0:
            print(
                "  Nothing here for release-please to read: every commit in the range is one of\n"
                "  the known-unparseable entries above, and each of those says where its content\n"
                "  was accounted for. A release run reporting 'no commits' is telling the truth.",
                file=stream,
            )
        return 0

    if considered == 0:
        print(
            "\ncommitcheck: release-please would consider ZERO of these commits and still\n"
            "exit 0. That is the failure this check exists for: a green run that released\n"
            "nothing, and a merged fix left unpublished.",
            file=stream,
        )
    for sha, message, faults in hijacked:
        subject = message.splitlines()[0] if message.splitlines() else "(empty)"
        print(
            f"\ncommitcheck: {sha[:12]} {subject} -- the body silently replaced this message",
            file=stream,
        )
        for line in faults:
            print(f"  {line}", file=stream)
    for sha, message, problems in dropped:
        subject = message.splitlines()[0] if message.splitlines() else "(empty)"
        report(problems, f"{sha[:12]} {subject}", stream=stream)
    return 1


def audit_commit(sha, *, engine, root=".", stream=None, pull_requests="auto", slug=None):
    """What release-please would do with one real commit, allowances aside.

    ``--range`` and ``--since-release`` answer a policy question -- is the release
    safe to cut -- and honour ``KNOWN_UNPARSEABLE`` accordingly. This answers the
    factual one: would release-please read *this* commit? An allowance would only
    get in the way of asking it about the commits that are on the list.
    """
    stream = sys.stdout if stream is None else stream
    result = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%H%n%B", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"commitcheck: {sha} is not in this repository", file=stream)
        return 1
    resolved_sha, _, message = result.stdout.partition("\n")
    resolved_sha = resolved_sha.strip()
    bodies, notes = resolve_bodies(
        [(resolved_sha, message)], mode=pull_requests, root=root, slug=slug, stream=stream
    )
    body = bodies.get(resolved_sha)
    text = preprocess_commit_message(message, body)
    subject = (text.splitlines() or ["(empty)"])[0]
    print(f"commitcheck: {resolved_sha[:12]}", file=stream)
    for note in notes:
        print(f"  {note}", file=stream)
    source = COMMIT_MESSAGE if text == message else OVERRIDE_ARTEFACT
    print(f"  release-please parses: {source}", file=stream)
    print(f"  which starts:          {subject[:76]}", file=stream)
    problems = check(message, engine=engine, pull_request_body=body)
    faults = override_faults(body)
    if not problems and not faults:
        print("  verdict: readable", file=stream)
        return 0
    for line in faults:
        print(f"  {line}", file=stream)
    if problems:
        return report(problems, f"{resolved_sha[:12]} {message.splitlines()[0]}", stream=stream)
    return 1


def audit_pull_request(number, *, engine, root=".", stream=None, slug=None):
    """Check the artefacts a merge will hand release-please, before it happens.

    The commit-msg hook validates what a developer wrote locally. Neither the
    body that can replace it nor the subject GitHub composes from the title ever
    passes under that hook, and the body is the one that has cost a release here.
    """
    stream = sys.stdout if stream is None else stream
    token = github_token()
    slug = slug or repo_slug(root)
    if not token or not slug:
        raise GitHubUnavailable("no GitHub token" if not token else "no owner/name")
    data = pull_request(number, slug=slug, token=token)
    title = data.get("title") or ""
    body = data.get("body") or ""
    problems, faults = check_pull_request(title, number, body, engine=engine)
    print(f"commitcheck: {slug}#{number}", file=stream)
    print(f"  squash subject GitHub offers: {title[:60]} (#{number})", file=stream)
    block = override_block(body)
    if block is None:
        print("  commit-override block in the body: none", file=stream)
    else:
        print(f"  commit-override block in the body: {block.splitlines()[0][:60]}", file=stream)
        print(
            "    that block REPLACES the merged commit message, whatever is typed at merge",
            file=stream,
        )
    if not problems and not faults:
        print("  verdict: readable", file=stream)
        return 0
    for line in faults:
        print(f"  {line}", file=stream)
    if problems:
        report(problems, f"{slug}#{number}", stream=stream)
    return 1


# ---------------------------------------------------------------------------
# Self-test.
#
# Built from shapes rather than from remembered verdicts: a checker that stopped
# detecting anything reads as a checker with nothing to report, which is how a
# guard dies quietly. `--demo` runs in CI ahead of the real audit for the same
# reason `leakcheck.py --demo` does.
# ---------------------------------------------------------------------------

#: Messages the grammar must refuse, each with the shape it is there to pin.
DEMO_REJECTED = {
    "nested parens at the start of a body line": (
        "fix(toon): a subject\n\n`Decimal(repr(value))` inside the canonical range\n"
    ),
    "a parenthesis left open at the end of a body line": (
        "fix: a subject\n\nrows_for(machine_identifier\nis required\n"
    ),
    "a parenthesis closed only on the following line": (
        "fix: a subject\n\nencode(document,\nindent=2)\n"
    ),
    "a bullet run into the parenthesised term": (
        "fix: a subject\n\n*array(allow_tabular(False))* is the change\n"
    ),
    "nested parens in the summary's own scope": "fix(toon(spec)): a subject\n",
    "no conventional-commit summary at all": "tidy up the encoder\n\nbody text\n",
    "an unparseable second conventional-commit section": (
        "fix: the first section\n\nbody\n\nfix: the second section\n\nf(g(h)) breaks it\n"
    ),
}

#: Messages that must keep working. Rich bodies are the point of this project's
#: history, so the guard is worth nothing if it also refuses them.
DEMO_ACCEPTED = {
    "the same phrase one word further along the line": (
        "fix(toon): a subject\n\nformats through `Decimal(repr(value))` inside the range\n"
    ),
    "a single parenthesised term at a line start": (
        "fix: a subject\n\n`Decimal(value)` is the shortest round-tripping form\n"
    ),
    "two flat parenthesised terms on one line": (
        "fix: a subject\n\nstars(rating) and unstars(value) agree in both directions\n"
    ),
    "an empty pair of parentheses": "fix: a subject\n\nitem.rate() clears a rating\n",
    "a markdown bullet, which has a space after the marker": (
        "fix: a subject\n\n- `array(allow_tabular=False)` is the change\n"
    ),
    "footers, trailers and a breaking-change note": (
        "feat(api)!: a subject\n\nbody text\n\nBREAKING CHANGE: the flag moved\nRefs: #10\n"
    ),
    "two conventional-commit sections, both parseable": (
        "fix(ci): the first section\n\nbody\n\nfix(toon): the second section\n\nmore body\n"
    ),
    "a rich body of the kind this fleet writes": (
        "fix(toon): satisfy a MUST the encoder was violating\n\n"
        "**Section 2 - canonical decimal form.** The spec makes decimal form mandatory\n"
        "for `0` and for `1e-6 <= |n| < 1e21`, so the encoder now formats through the\n"
        "shortest round-tripping digits, `Decimal(repr(value))`, inside that range and\n"
        "defers to `json.dumps` outside it, where an exponent is permitted.\n\n"
        "Verified against a built wheel, not only the checkout.\n"
    ),
}


#: Pull request shapes, which no commit-msg hook ever sees. ``(title, number,
#: body)``. The bodies are where the damage is: the body can replace the message
#: entirely, and the first entry is the shape that actually cost this release --
#: a paragraph *about* the marker, which the marker's own lookup cannot tell from
#: an instruction to use one.
DEMO_PULL_REQUESTS_REJECTED = {
    "prose naming the marker, taken as an instruction to use one": (
        "fix(ci): a subject",
        12,
        "## Intent\n\nrelease-please replaces the whole message with a "
        + OVERRIDE_START
        + " block from the pull request body when there is one, which no hook can see.\n",
    ),
    "a closed override block that is not a conventional commit": (
        "fix(ci): a subject",
        12,
        f"## Intent\n\n{OVERRIDE_START}\nreleased the thing\n{OVERRIDE_END}\n",
    ),
    "an override block that is a conventional commit the parser refuses": (
        "fix(ci): a subject",
        12,
        f"{OVERRIDE_START}\nfix: a subject\n\n`a(b(c))` at the line start\n{OVERRIDE_END}\n",
    ),
    "an accidental mention whose following prose happens to parse": (
        "fix(ci): a subject",
        12,
        "## Intent\n\nsee " + OVERRIDE_START + "\n\nfix: this paragraph parses by luck\n",
    ),
    "a title that is not a conventional-commit summary": (
        "prevent the release workflow skipping commits",
        12,
        "## Intent\n\nordinary prose.\n",
    ),
}

#: Pull request shapes that must keep working, including the deliberate override
#: the marker exists for. A guard that refused those would be answered by not
#: running it.
DEMO_PULL_REQUESTS_ACCEPTED = {
    "an ordinary pull request with no marker in the body": (
        "fix(ci): a subject",
        12,
        "## Intent\n\nA rich body with `Decimal(repr(value))` mid-line and (parentheses).\n",
    ),
    "a deliberate, closed override that is a conventional commit": (
        "chore: internal title nobody wants in the changelog",
        12,
        f"## Intent\n\nprose\n\n{OVERRIDE_START}\nfix(toon): restate the dropped fix\n\n"
        f"body text\n{OVERRIDE_END}\n\nmore prose\n",
    ),
    "a body that ends on the marker, so the override is empty": (
        "fix(ci): a subject",
        12,
        "## Intent\n\nthe marker is " + OVERRIDE_START,
    ),
    "a breaking-change title": ("feat(api)!: the flag moved", 12, "## Intent\n\nprose.\n"),
}


def run_demo(engine, stream=None):
    """Prove the checker still rejects the bad shapes and still accepts the good."""
    stream = sys.stdout if stream is None else stream
    failures = []
    for label, message in DEMO_REJECTED.items():
        if not check(message, engine=engine):
            failures.append(f"accepted a message it must reject: {label}")
    for label, message in DEMO_ACCEPTED.items():
        problems = check(message, engine=engine)
        if problems:
            failures.append(f"rejected a message it must accept: {label} ({problems[0].error})")
    for label, (title, number, body) in DEMO_PULL_REQUESTS_REJECTED.items():
        problems, faults = check_pull_request(title, number, body, engine=engine)
        if not problems and not faults:
            failures.append(f"accepted a pull request it must reject: {label}")
    for label, (title, number, body) in DEMO_PULL_REQUESTS_ACCEPTED.items():
        problems, faults = check_pull_request(title, number, body, engine=engine)
        if problems or faults:
            detail = problems[0].error if problems else faults[0]
            failures.append(f"rejected a pull request it must accept: {label} ({detail})")
    total = (
        len(DEMO_REJECTED)
        + len(DEMO_ACCEPTED)
        + len(DEMO_PULL_REQUESTS_REJECTED)
        + len(DEMO_PULL_REQUESTS_ACCEPTED)
    )
    if failures:
        print(f"commitcheck --demo: {len(failures)} of {total} cases wrong ({engine})", file=stream)
        for failure in failures:
            print(f"  {failure}", file=stream)
        return 1
    print(
        f"commitcheck --demo: {len(DEMO_REJECTED) + len(DEMO_PULL_REQUESTS_REJECTED)} rejected, "
        f"{len(DEMO_ACCEPTED) + len(DEMO_PULL_REQUESTS_ACCEPTED)} accepted, "
        f"as expected ({engine} engine)",
        file=stream,
    )
    return 0


def list_rules(engine, stream=None):
    stream = sys.stdout if stream is None else stream
    print("commitcheck: what this refuses, and why\n", file=stream)
    print(__doc__.split("Usage:")[0].strip(), file=stream)
    print(f"\nengine in use: {engine}", file=stream)
    print(f"vendored parser: {VENDORED_PARSER.relative_to(REPO_ROOT)}", file=stream)
    print(f"  present: {'yes' if VENDORED_PARSER.exists() else 'no'}", file=stream)
    print(f"  node on PATH: {'yes' if node_available() else 'no'}", file=stream)
    token = github_token()
    slug = repo_slug()
    print(
        f"\npull request bodies: {'reachable' if token and slug else 'NOT reachable'}", file=stream
    )
    print(f"  repository: {slug or '(unknown)'}", file=stream)
    print(f"  token: {'present' if token else 'absent -- export GITHUB_TOKEN'}", file=stream)

    print("\nknown-unparseable commits, exempt from --range and --since-release:", file=stream)
    if not KNOWN_UNPARSEABLE:
        print("  (none)", file=stream)
    for sha, reason in KNOWN_UNPARSEABLE.items():
        print(f"  {sha}", file=stream)
        print(f"    {reason}", file=stream)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="commitcheck.py",
        description="Reject a commit message release-please cannot parse.",
    )
    parser.add_argument("--commit-msg", metavar="PATH", help="check a commit message file")
    parser.add_argument("--message", metavar="TEXT", help="check a literal message")
    parser.add_argument("--range", metavar="A..B", help="check every commit message in a range")
    parser.add_argument(
        "--since-release",
        action="store_true",
        help="check every commit since the tag named by .release-please-manifest.json",
    )
    parser.add_argument(
        "--commit", metavar="SHA", help="check one commit as release-please would read it"
    )
    parser.add_argument(
        "--pull-request",
        metavar="N",
        help="check what a pull request will hand release-please at merge",
    )
    parser.add_argument(
        "--pull-requests",
        choices=("auto", "require", "skip"),
        default="auto",
        help=(
            "consult pull request bodies, which can replace a commit message: "
            "auto (default), require (fail without them), skip"
        ),
    )
    parser.add_argument(
        "--repo", metavar="OWNER/NAME", help="repository to ask about (default: origin)"
    )
    parser.add_argument("--rules", action="store_true", help="explain the rule and exit")
    parser.add_argument("--demo", action="store_true", help="self-test and exit")
    parser.add_argument(
        "--engine",
        choices=("auto", "node", "python"),
        default="auto",
        help="auto (default) runs the vendored upstream parser when node is available",
    )
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)

    engine = resolve_engine(args.engine)

    if args.rules:
        return list_rules(engine)
    if args.demo:
        return run_demo(engine)

    audit_kwargs = {
        "engine": engine,
        "root": args.root,
        "pull_requests": args.pull_requests,
        "slug": args.repo,
    }
    try:
        if args.since_release:
            return audit_range(release_range(args.root), **audit_kwargs)
        if args.range:
            return audit_range(args.range, **audit_kwargs)
        if args.commit:
            return audit_commit(args.commit, **audit_kwargs)
        if args.pull_request:
            return audit_pull_request(
                args.pull_request, engine=engine, root=args.root, slug=args.repo
            )
    except GitHubUnavailable as exc:
        print(
            f"commitcheck: cannot read pull request bodies from GitHub ({exc}).\n"
            "  release-please replaces a commit message with the override block in its pull\n"
            "  request body, so without them this check cannot see the text release-please\n"
            "  will read. Refusing to report a verdict it cannot support.",
            file=sys.stderr,
        )
        return 1

    if args.commit_msg:
        message = Path(args.commit_msg).read_text(encoding="utf-8", errors="replace")
        label = "this commit message"
    elif args.message is not None:
        message = args.message
        label = "this message"
    else:
        parser.error(
            "nothing to check: pass --commit-msg, --message, --commit, --pull-request, "
            "--range or --since-release"
        )

    # A message is only what git will record: everything from the first comment
    # line onwards is stripped by git before the commit exists, and refusing a
    # commit for a parenthesis inside the instructions git itself wrote would be
    # a guard nobody could satisfy.
    message = "\n".join(line for line in message.splitlines() if not line.startswith("#"))

    problems = check(message, engine=engine)
    if not problems:
        return 0
    return report(problems, label)


if __name__ == "__main__":
    sys.exit(main())
