# Evals

Homework samples with what a good answer must contain, and a scorer, so model and prompt changes
can be compared. Real agent runs cost model credits: run them nightly or on PRs labelled
`run-evals`, never on every PR. Tests only use the fake solver and the fake judge.

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
