# Evals

Homework samples with what a good answer must contain, and a scorer, so model and prompt changes
can be compared. Real agent runs cost model credits: run them nightly or on PRs labelled
`run-evals`, never on every PR. Tests only use the fake solver and the fake judge.

## Run
```
uv run python -m evals run out/            # solve every sample, writes out/<id>.md (fake solver for now)
uv run python -m evals score out/ --json results.json
uv run python -m evals score out/ --judge fake   # also score rubrics
```
`--samples <dir>` picks another sample folder (default `evals/samples`).

`score` prints one row per sample and writes the same results as JSON. A missing `<id>.md` fails
that sample.

Plugging in the real thing: a solver is any `Solver` (`evals/src/evals/solve.py`) that turns a
sample into Markdown, e.g. by calling the Jobs API over HTTP; a judge is any `Judge`
(`judge.py`). Add them to `SOLVERS`/`JUDGES` in `__main__.py`. Math answers must end with
`\boxed{...}`.

## Checks
Deterministic, run on every output:
- `not_empty`
- `math_syntax`: math delimiters, braces, `\begin`/`\end` and `\left`/`\right` balance, so KaTeX can
  render it. Inline `$...$` follows pandoc's rule; a dollar sign for money must be written `\$`.
  Code spans are ignored. Unknown commands are not caught.
- `final_answer` (math): the last `\boxed{...}` must match one of `answers`. Normalised first:
  spaces, everything up to the last `=` (`f'(1) = 1`), anything after `\approx`, a trailing
  `\text{unit}`, `\dfrac`, `\frac12`, and equal numbers (`\frac{1}{2}` = `0.5`). A comma between
  digits is a decimal comma (`2,5`) unless `language` is `en`, where it separates thousands.
  One answer per sample: sets of solutions are not supported yet.
- `length`: word count within `min_words`/`max_words`, when set.

Rubrics are scored by an LLM judge (`evals/src/evals/judge.py`): it gets `judge_prompt(...)` and
returns points per criterion. Only `FakeJudge` exists for now; a real one must never run in tests.

## Samples
One YAML file per sample in `samples/<subject>/<id>.yaml` (not `.yml`); the file name must equal
`id`. The `Sample` model in `evals/src/evals/models.py` validates every field:

```yaml
id: hs-linear-equation          # lowercase, digits, dashes
subject: math                   # math | essay
level: high_school              # high_school | college
language: en                    # language of the task and expected answer
prompt: Solve for x. 3x + 7 = 22
image: hs-linear-equation.png   # optional, relative to this file, inside samples/
answers: ["5"]                  # math: accepted final answers (any one counts)
rubric:                         # essays (optional for math)
  - {description: "Clear thesis", points: 2}
min_words: 300                  # optional
max_words: 600                  # optional
source: synthetic               # synthetic | real
```

### Adding real homework
1. Remove everything personal before the file is committed: names (student, teacher, classmates),
   school, class, city, dates of birth, grades, handwriting signatures, faces. Crop or redact photos.
   Git history keeps files forever, so this cannot be fixed later.
2. Only use homework the student agreed to share.
3. Set `source: real`, write the expected answer or rubric yourself, and check that
   `uv run pytest evals` passes.
