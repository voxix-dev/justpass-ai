import resource
import time
from pathlib import Path

from pytest import MonkeyPatch

from sandbox.runner import Limits, run_code

# The process limit counts every process of the user running the tests: keep the current one.
# The integration test checks the real one on a sandbox actor.
NPROC = resource.getrlimit(resource.RLIMIT_NPROC)[0]
FAST = Limits(timeout_s=2, max_output_bytes=1000, max_memory_bytes=256 << 20, max_processes=NPROC)


def test_runs_a_script_and_returns_its_output():
    result = run_code("import sys\nprint(6 * 7)\nprint('oops', file=sys.stderr)", FAST)
    assert (result.exit_code, result.stdout, result.stderr) == (0, "42\n", "oops\n")
    assert not result.timed_out and not result.output_truncated


def test_reports_the_traceback_and_exit_code():
    result = run_code("1 / 0", FAST)
    assert result.exit_code == 1
    assert result.stderr.startswith('Traceback (most recent call last):\n  File "main.py", line 1')
    assert "ZeroDivisionError" in result.stderr


def test_reports_syntax_errors_in_main_py():
    result = run_code("print(", FAST)
    assert result.exit_code == 1
    assert result.stderr.startswith('  File "main.py", line 1')


def test_kills_a_script_that_runs_too_long_and_keeps_what_it_printed():
    result = run_code("print('partial 42')\nwhile True: pass", FAST)
    assert result.timed_out
    assert result.exit_code < 0
    assert result.stdout == "partial 42\n"


def test_the_script_cannot_raise_its_limits():
    result = run_code("import resource\nresource.setrlimit(resource.RLIMIT_AS, (-1, -1))", FAST)
    assert "ValueError: not allowed to raise maximum limit" in result.stderr


def test_caps_the_output():
    result = run_code("while True: print('x' * 100)", FAST)
    assert result.output_truncated
    assert len(result.stdout) == 1000
    assert "File too large" in result.stderr
    assert not result.timed_out


def test_caps_memory():
    result = run_code("x = bytearray(512 * 1024 * 1024)", FAST)
    assert result.exit_code == 1
    assert "MemoryError" in result.stderr


def test_each_run_gets_a_fresh_dir_and_none_of_the_server_env(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("SECRET", "s3cret")
    assert run_code("open('note.txt', 'w').write('hi')", FAST).exit_code == 0
    code = "import os\nprint(os.path.exists('note.txt'), os.environ.get('SECRET'))"
    assert run_code(code, FAST).stdout == "False None\n"


def test_kills_children_left_running():
    code = "import subprocess, sys\n"
    code += "print(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']).pid)"
    result = run_code(code, FAST)
    status = Path(f"/proc/{int(result.stdout)}/status")
    for _ in range(20):  # SIGKILL is delivered asynchronously
        if not status.exists() or "State:\tZ" in status.read_text():  # gone, or a dead zombie
            return
        time.sleep(0.1)
    raise AssertionError("the child is still running")
