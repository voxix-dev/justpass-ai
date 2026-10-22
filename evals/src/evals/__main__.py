"""python -m evals run|score. See evals/README.md."""

import argparse
from pathlib import Path

from evals.dataset import load_samples
from evals.judge import FakeJudge, Judge
from evals.score import format_table, score_dir
from evals.solve import Solver, fake_solve, run

SOLVERS: dict[str, Solver] = {"fake": fake_solve}
JUDGES: dict[str, Judge] = {"fake": FakeJudge()}
DEFAULT_SAMPLES = Path(__file__).parents[2] / "samples"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="solve every sample, write <outputs>/<id>.md")
    run_cmd.add_argument("outputs", type=Path)
    run_cmd.add_argument("--solver", choices=SOLVERS, default="fake")

    score_cmd = commands.add_parser("score", help="score a directory of outputs")
    score_cmd.add_argument("outputs", type=Path)
    score_cmd.add_argument("--json", type=Path, help="also write the results here")
    score_cmd.add_argument("--judge", choices=JUDGES, help="score rubrics too (default: off)")

    args = parser.parse_args(argv)
    samples = load_samples(args.samples)
    if args.command == "run":
        run(samples, SOLVERS[args.solver], args.outputs)
        print(f"wrote {len(samples)} outputs to {args.outputs}")
        return
    report = score_dir(samples, args.outputs, JUDGES[args.judge] if args.judge else None)
    print(format_table(report))
    if args.json:
        args.json.write_text(report.model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
