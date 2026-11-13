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
    container = 0  # where the open fence's container starts; a closing one may sit 3 columns in

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
                and len(opening[1]) <= container + 3
                and set(line.strip()) == {fence[0]}
                and len(line.strip()) >= len(fence)
            ):
                fence = ""
        elif opening:
            flush()
            out.append(line)
            indent = len(opening[1])
            container = indent if indent > 3 else 0  # deeper than 3 only inside a list item
            fence = opening[2]
        else:
            prose.append(line)
    flush()
    return "".join(out)
