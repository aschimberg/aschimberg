"""Split a large simulation across CPU cores (fork), with per-chunk seeds so results stay deterministic."""
import multiprocessing as mp
import os

MIN_PARALLEL = 20000
_POOL = None


def _pool():
    global _POOL
    if _POOL is None:
        _POOL = mp.get_context("fork").Pool(min(4, os.cpu_count() or 1))
    return _POOL


def chunks(fn, n, args):
    """fn(*args, n_chunk, chunk_index) -> list; results concatenated in chunk order."""
    cores = min(4, os.cpu_count() or 1)
    if n < MIN_PARALLEL or cores < 2 or os.environ.get("SIMS_SERIAL"):
        return [fn(*args, n, 0)]
    sizes = [n // cores + (1 if i < n % cores else 0) for i in range(cores)]
    return _pool().starmap(fn, [(*args, s, i) for i, s in enumerate(sizes)])
