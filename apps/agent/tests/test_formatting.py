import pytest

from agent.formatting import normalize_markdown


def test_display_math_goes_on_its_own_lines():
    assert normalize_markdown(r"Zatem \[x^2 + 1\] czyli") == "Zatem \n$$\nx^2 + 1\n$$\n czyli"


def test_multiline_display_math():
    text = "A\n\\[\n  a = b\n  c = d\n\\]\nB"
    assert normalize_markdown(text) == "A\n\n$$\na = b\n  c = d\n$$\n\nB"


def test_text_on_a_dollar_fence_line_moves_inside_the_block():
    text = "## Odpowiedzi\n\n$$\\boxed{\n\\begin{array}{ll}\n1 & 2\n\\end{array}\n}$$\n"
    assert normalize_markdown(text) == (
        "## Odpowiedzi\n\n$$\n\\boxed{\n\\begin{array}{ll}\n1 & 2\n\\end{array}\n}\n$$\n"
    )


def test_a_dollar_block_opened_mid_line_starts_on_a_new_line():
    assert normalize_markdown("Zatem $$x\n=y$$.") == "Zatem \n$$\nx\n=y\n$$\n."


def test_indented_math_does_not_indent_the_closing_dollars():
    assert normalize_markdown("$$x =\n    1$$\n\nAfter") == "$$\nx =\n1\n$$\n\nAfter"


def test_a_dollar_block_in_a_blockquote_stays_in_it():
    text = "> $$\\boxed{\n> x\n> }$$\n"
    assert normalize_markdown(text) == "> $$\n> \\boxed{\n> x\n> }\n> $$\n"


def test_a_quote_marker_without_a_space_is_not_repeated():
    assert normalize_markdown("> $$x\n>y$$\n") == "> $$\n> x\n> y\n> $$\n"


def test_a_dollar_block_in_a_list_item_stays_in_it():
    text = "1. $$\\boxed{\n   x\n   }$$\n"
    assert normalize_markdown(text) == "1. $$\n   \\boxed{\n   x\n   }\n   $$\n"


@pytest.mark.parametrize(
    "text",
    [
        "A\n\n$$\nx\n=y\n$$\n\nB",
        "1. Krok\n\n   $$\n   x\n   =y\n   $$\n",
        "> $$\n> x\n> $$\n",
        "- $$\n  x\n\n  y\n  $$\n",
        "so $$x = 1$$ here",
    ],
)
def test_well_formed_dollar_math_is_unchanged(text: str):
    assert normalize_markdown(text) == text


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
