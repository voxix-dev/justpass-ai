"""Run one Python script in a child process with hard limits.

Each run gets a fresh process, an empty temp dir as cwd and HOME, and a clean environment. The
limits are set inside the child before the script starts, so the script can't raise them. Output
goes to unnamed files capped by RLIMIT_FSIZE (as is every file the script writes): past the cap,
writes fail with "File too large" instead of filling the actor's memory.

Substrate runs the server as root (inside gVisor), so the script runs as `nobody`: it can't
signal the server or read its memory, and the process limit counts only the script's processes.
A process it leaves behind (e.g. after setsid) can still reach later runs of the same job: the
job's sandbox actor is the boundary.
"""

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO

from pydantic import BaseModel

from shared.code_run import CodeRunResult


class Limits(BaseModel):
    timeout_s: int = 10  # wall clock
    max_output_bytes: int = 16_000  # per stream; this goes back into a model's context
    max_memory_bytes: int = 512 * 1024 * 1024  # address space; numpy, scipy and sympy fit in it
    # Processes and threads of the script's user: stops fork bombs. Forked processes that each
    # fill their memory can still crash this one job's actor.
    max_processes: int = 16


NOBODY = 65534


# argv: memory, file size, CPU seconds, processes. The excepthook hides this code's frame from
# tracebacks, so the model sees only main.py.
_BOOT = """\
import resource, sys
limits = resource.RLIMIT_AS, resource.RLIMIT_FSIZE, resource.RLIMIT_CPU, resource.RLIMIT_NPROC
for limit, value in zip(limits, sys.argv[1:]):
    resource.setrlimit(limit, (int(value), int(value)))
sys.argv = ["main.py"]
def hide_boot(kind, error, tb):
    sys.__excepthook__(kind, error.with_traceback(tb and tb.tb_next), tb and tb.tb_next)
sys.excepthook = hide_boot
with open("main.py") as f:
    code = compile(f.read(), "main.py", "exec")
exec(code, {"__name__": "__main__", "__file__": "main.py", "__builtins__": __builtins__})
"""


def run_code(code: str, limits: Limits) -> CodeRunResult:
    rlimits = [
        limits.max_memory_bytes,
        limits.max_output_bytes + 1,
        limits.timeout_s + 1,  # CPU seconds: a backstop for the wall clock
        limits.max_processes,
    ]
    as_root = os.geteuid() == 0
    with (
        tempfile.TemporaryDirectory() as work,
        tempfile.TemporaryFile() as out,
        tempfile.TemporaryFile() as err,
    ):
        os.chmod(work, 0o777)  # so nobody can write in it
        with open(os.path.join(work, "main.py"), "w") as f:
            f.write(code)
        proc = subprocess.Popen(
            [sys.executable, "-I", "-u", "-c", _BOOT, *map(str, rlimits)],
            cwd=work,
            env={"HOME": work, "PATH": "/usr/bin:/bin", "OPENBLAS_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,  # its own process group, so we can kill all it started
            user=NOBODY if as_root else None,
            group=NOBODY if as_root else None,
            extra_groups=[] if as_root else None,
        )
        try:
            proc.wait(timeout=limits.timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # also any child it left running
        proc.wait()
        _reap_orphans()
        stdout, out_cut = _read(out, limits.max_output_bytes)
        stderr, err_cut = _read(err, limits.max_output_bytes)
    return CodeRunResult(
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        output_truncated=out_cut or err_cut,
    )


def _reap_orphans() -> None:
    """The server is the actor's PID 1, so the script's orphans become its children. Zombies
    count against the process limit until reaped, and SIGKILL takes a moment to land. Runs are
    one at a time, so every child is the last run's. Wait at most a second for them."""
    deadline = time.monotonic() + 1
    with contextlib.suppress(ChildProcessError):  # no children left
        while time.monotonic() < deadline:
            if not os.waitpid(-1, os.WNOHANG)[0]:
                time.sleep(0.01)


def _read(f: BinaryIO, cap: int) -> tuple[str, bool]:
    f.seek(0)
    data = f.read(cap + 1)
    return data[:cap].decode(errors="replace"), len(data) > cap
