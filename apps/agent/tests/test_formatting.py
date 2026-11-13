import pytest

from agent.formatting import normalize_markdown


def test_display_math_goes_on_its_own_lines():
    assert normalize_markdown(r"Zatem \[x^2 + 1\] czyli") == "Zatem \n$$\nx^2 + 1\n$$\n czyli"


def test_multiline_display_math():
    text = "A\n\\[\n  a = b\n  c = d\n\\]\nB"
    assert normalize_markdown(text) == "A\n\n$$\na = b\n  c = d\n$$\n\nB"


def test_inline_math_is_stripped():
    assert normalize_markdown(r"so \( x + 1 \) and \(y\)") == "so $x + 1$ and $y$"


def test_polish_text():
    assert normalize_markdown(r"Pochodna \(f'(x)=2x\)") == "Pochodna $f'(x)=2x$"


def test_code_fence_is_left_alone():
    text = "```\n\\(x\\) \\[y\\]\n```\n\\(z\\)\n"
    assert normalize_markdown(text) == "```\n\\(x\\) \\[y\\]\n```\n$z$\n"


def test_longer_fence_is_not_closed_by_a_shorter_one():
    text = "````\n```\n\\(x\\)\n````\n"
    assert normalize_markdown(text) == text


def test_tilde_fence_is_left_alone():
    text = "~~~\n\\(x\\)\n~~~"
    assert normalize_markdown(text) == text


def test_inline_code_is_left_alone():
    assert normalize_markdown(r"use `\(x\)` or \(y\)") == r"use `\(x\)` or $y$"


def test_line_break_in_math_is_left_alone():
    text = "$$\n\\begin{aligned}\na &= 1 \\\\[2mm]\nb &= 2\n\\end{aligned}\n$$"
    assert normalize_markdown(text) == text


def test_text_without_math_is_unchanged():
    text = "## Title\n\n- one\n- **two**\n\nCost is \\$5 and $x$ and $$y$$."
    assert normalize_markdown(text) == text


def test_fence_inside_a_list_item_is_left_alone():
    text = '1. Code:\n\n    ~~~python\n    pattern = r"\\(x\\)"\n    ~~~\n\n2. Then \\(y\\)'
    assert normalize_markdown(text) == text.replace("\\(y\\)", "$y$")


@pytest.mark.parametrize("opening", ["```", "   ```"])
def test_a_marker_four_columns_in_does_not_close_a_fence(opening: str):
    text = f"{opening}\n    ```\n\\(x\\)\n```\nThen \\(y\\)"
    assert normalize_markdown(text) == text.replace("\\(y\\)", "$y$")
