"""
Content normalization for change detection.

Raw page text contains a lot of material that changes on every fetch without
any underlying activity: relative timestamps ("2h ago"), engagement counters,
clock times, cache-busting tokens. Hashing that text directly means every
check looks like a change, so every check sends an alert.

Normalization replaces each class of volatile text with a stable placeholder
before the content is hashed or diffed. The hash then only moves when the
substance moves, and the diff shows the substance rather than a wall of
counter churn.

Rules are grouped by name so a target can opt out of one group without losing
the others. See `parse_ignore_spec` for the per-target specification format.
"""

import re

# Placeholders keep line structure intact so unified diffs stay readable.
T = "<time>"
D = "<date>"
N = "<count>"
K = "<token>"

# Rule groups: name -> [(pattern, replacement), ...]
# Order within a group matters; groups are applied in the order listed here.
NOISE_RULES: dict[str, list[tuple[re.Pattern, str]]] = {
    # "2 hours ago", "5 min ago", "just now", "yesterday", Twitter's "· 2h"
    "relative_time": [
        (re.compile(
            r"\b(?:a|an|\d[\d,.]*)\s*"
            r"(?:second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago\b",
            re.I), T),
        (re.compile(r"\ba\s+few\s+seconds\s+ago\b", re.I), T),
        (re.compile(r"\b(?:just\s+now|moments?\s+ago|yesterday|today)\b", re.I), T),
        (re.compile(r"\bedited\s+\d+[smhdwy]\s+ago\b", re.I), T),
        # Compact forms only when clearly acting as a timestamp separator,
        # so we don't eat legitimate text like "3m" in a sentence.
        (re.compile(r"[·•|]\s*\d+[smhdwy]\b", re.I), "· " + T),
    ],

    # Clock times and calendar dates in the common rendered formats.
    "timestamp": [
        (re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b"), D),
        (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), D),
        (re.compile(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
            r"\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b", re.I), D),
        (re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\b"), T),
    ],

    # "1.2K views", "342 likes", "5.3M followers" — the single largest source
    # of false positives on social profiles.
    "engagement": [
        (re.compile(
            r"\b\d[\d,.]*\s*[KkMmBb]?\s+"
            r"(views?|likes?|comments?|repl(?:y|ies)|reposts?|retweets?|shares?|"
            r"followers?|following|subscribers?|upvotes?|downvotes?|points?|"
            r"reactions?|impressions?|quotes?|bookmarks?|members?|watching|"
            r"posts?|tweets?|photos?|videos?|articles?|jobs?|connections?|"
            r"ratings?|reviews?|stars?|forks?|downloads?)\b",
            re.I), N + r" \1"),
        # Trailing form: "Views: 1.2K", or a label alone on its own line.
        # Deliberately narrow — a bare "comments 2" would otherwise swallow an
        # unrelated number that merely happens to follow the label, which can
        # hide a real change.
        (re.compile(
            r"\b(views?|likes?|comments?|followers?|following|subscribers?)"
            r"\s*[:\-]\s*\d[\d,.]*\s*[KkMmBb]?\b", re.I), r"\1: " + N),
        (re.compile(
            r"^(views?|likes?|comments?|followers?|following|subscribers?)"
            r"\s+\d[\d,.]*\s*[KkMmBb]?$", re.I | re.M), r"\1 " + N),
    ],

    # Long opaque strings: nonces, cache keys, signed URL params, session ids.
    "tokens": [
        (re.compile(r"\b[0-9a-f]{32,}\b", re.I), K),
        (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), K),
    ],
}

# Applied after the rule groups, always.
_MULTISPACE = re.compile("[ \\t\\xa0]+")
_ZERO_WIDTH = re.compile("[\\u200b-\\u200f\\u2060\\ufeff]")


def parse_ignore_spec(raw: str) -> tuple[list[re.Pattern], set[str], list[str]]:
    """
    Parse a per-target ignore specification.

    Format — one directive per line:
        # comment              ignored
        !engagement            disable the built-in "engagement" rule group
        Sponsored.*            a custom regex; matches are dropped entirely

    Custom patterns are matched case-insensitively against the text *after*
    the built-in rules have run, so they should be written against the
    placeholder form (e.g. `^#\\w+ <count> posts$`, not `^#\\w+ 14.2K posts$`).
    That is also the form shown in the dashboard's diff view.

    Returns (custom_patterns, disabled_groups, errors). Invalid regexes are
    skipped and reported in `errors` rather than raising, so one bad line
    can't stop a target from being monitored.
    """
    patterns: list[re.Pattern] = []
    disabled: set[str] = set()
    errors: list[str] = []

    for lineno, line in enumerate((raw or "").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            group = line[1:].strip()
            if group in NOISE_RULES:
                disabled.add(group)
            else:
                errors.append(
                    f"line {lineno}: unknown rule group '{group}' "
                    f"(available: {', '.join(sorted(NOISE_RULES))})"
                )
            continue
        try:
            # MULTILINE so ^/$ anchor to lines, which is how anyone writing a
            # pattern against page text expects them to behave.
            patterns.append(re.compile(line, re.I | re.MULTILINE))
        except re.error as e:
            errors.append(f"line {lineno}: invalid regex — {e}")

    return patterns, disabled, errors


def validate_ignore_spec(raw: str) -> list[str]:
    """Return a list of human-readable problems with an ignore spec."""
    return parse_ignore_spec(raw)[2]


def normalize(text: str, ignore_spec: str = "") -> str:
    """
    Reduce raw page text to a stable form suitable for hashing and diffing.

    Volatile substrings are replaced with placeholders, custom ignore patterns
    are stripped, whitespace is collapsed, and consecutive duplicate lines are
    folded. Returns "" if nothing survives.
    """
    if not text:
        return ""

    custom, disabled, _ = parse_ignore_spec(ignore_spec)

    text = _ZERO_WIDTH.sub("", text)

    for group, rules in NOISE_RULES.items():
        if group in disabled:
            continue
        for pattern, replacement in rules:
            text = pattern.sub(replacement, text)

    for pattern in custom:
        text = pattern.sub("", text)

    lines: list[str] = []
    previous = None
    for line in text.splitlines():
        line = _MULTISPACE.sub(" ", line).strip()
        if not line or line == previous:
            continue
        lines.append(line)
        previous = line

    return "\n".join(lines)
