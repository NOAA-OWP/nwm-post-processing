#!/usr/bin/env python3
"""
Example for how to compute pi via MPI

The number pi can be approximated via numerical integration with the simple midpoint rule, i.e.
1 / n * the sum from i = 1 to n of 4 divided by 1 plus the square of 1 / n times i minus 0.5

Run via:
    mpiexec python /path/to/pi.py

If you get:
    bash: mpiexec: command not found

Make sure you've loaded the mpi module like:
    module load mpi

If you only have one rank, set the following:
    export MPI4PY_FUTURES_MAX_WORKERS=4

4 is just a suggestion - change it to whatever desired. The default is 1.

The default for `max_workers` for the MPIPoolExecutor is None and the fallback is:
    int(os.environ.get('MPI4PY_FUTURES_MAX_WORKERS', 1))
"""
import os
import math
import sys
import random

from concurrent.futures import Future

from mpi4py import MPI
from mpi4py.futures import MPIPoolExecutor
from mpi4py.futures import wait
from mpi4py.futures import get_comm_workers

RESOLUTION: int = 256
WORKER_COUNT: int = 16

def compute_pi(step: int, total: int) -> float:
    """
    Compute pi for this division of the formula
    """
    print(f"Calculating a piece of pi at rank {step} (PID {os.getpid()})", flush=True)

    # Local computation
    interval_width: float = 1.0 / RESOLUTION
    partial_sum: float = 0.0

    for rank_index in range(step, RESOLUTION + 1, total):
        midpoint: float = interval_width * (rank_index - 0.5)
        partial_sum += 4.0 / (1.0 + midpoint**2)

    pi_partial: float = partial_sum * interval_width

    print(f"Rank {step} calculated {pi_partial:.16f} from PID {os.getpid()}", flush=True)

    return pi_partial

def main() -> int:
    """
    Main application logic

    1. Start the executor
    2. Get a random number of workers to actually use to demonstrate that not ALL workers have to be used or
        that some may be used multiple times
    3. submit jobs to each process
    4. Get the results from each
    5. Sum them
        - The tutorial showed doing them within the passed function. I changed that up since this is the more common pattern
    """
    print(f"Calling main from PID: {os.getpid()}", flush=True)
    try:
        with MPIPoolExecutor() as executor:
            futures: list[Future[float]] = [
                executor.submit(compute_pi, step=rank, total=executor.num_workers)
                for rank in range(1, executor.num_workers + 1)
            ]

            # The tutorial says to call 'wait'. Not necessary because '.result' will do that for you
            #wait(futures)

            results: list[float] = [future.result() for future in futures]
            pi: float = sum(results)

            print(
                f"pi: {pi:.16f}, error: {abs(pi - math.pi):.3e} ({RESOLUTION:d} intervals, {executor.num_workers:d} steps)",
                flush=True
            )

    except BaseException as e:
        print(f"Failed to calculate pi: {e}", file=sys.stderr, flush=True)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
