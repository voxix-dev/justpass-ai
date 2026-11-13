"""Markdown clean-up for model answers.

The web app renders `$...$` and `$$...$$` math only, so the LaTeX delimiters `\\(...\\)` and
`\\[...\\]` are rewritten to those.
"""

import re

_FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})")  # any indent: fences nest in list items

# Alternatives are tried left to right at each position, so code spans and `$$` blocks are matched
# (and left alone) before a delimiter inside them can be.
_SPAN = re.compile(
    r"""
    (?P<code>(?<!`)(?P<ticks>`+)(?!`).+?(?<!`)(?P=ticks)(?!`))
    | (?P<dollars>\$\$.*?\$\$)
    | (?<!\\)\\\[(?P<display>.*?)(?<!\\)\\\]
    | (?<!\\)\\\((?P<inline>.*?)(?<!\\)\\\)
    """,
    re.DOTALL | re.VERBOSE,
)


def _replace(match: re.Match[str]) -> str:
    if match["display"] is not None:
        return f"\n$$\n{match['display'].strip()}\n$$\n"
    if match["inline"] is not None:
        return f"${match['inline'].strip()}$"
    return match[0]


def normalize_markdown(text: str) -> str:
    """Rewrite `\\[...\\]` as `$$` display math and `\\(...\\)` as `$...$` inline math.

    Fenced code blocks, inline code spans, existing `$$...$$` blocks and escaped `\\\\[` (a LaTeX
    line break) are left as they are.
    """
    out: list[str] = []
    prose: list[str] = []
    fence = ""
    indent = 0  # of the open fence; a closing one may sit at most 3 columns deeper

    def flush() -> None:
        if prose:
            out.append(_SPAN.sub(_replace, "".join(prose)))
            prose.clear()

    for line in text.splitlines(keepends=True):
        opening = _FENCE.match(line)
        if fence:
            out.append(line)
            if (
                opening
                and len(opening[1]) <= indent + 3
                and set(line.strip()) == {fence[0]}
                and len(line.strip()) >= len(fence)
            ):
                fence = ""
        elif opening:
            flush()
            out.append(line)
            indent, fence = len(opening[1]), opening[2]
        else:
            prose.append(line)
    flush()
    return "".join(out)
