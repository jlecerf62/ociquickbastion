import sys
import subprocess
from typing import Iterable, List
from difflib import get_close_matches


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def error_exit(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def fuzzy_filter(items: Iterable[str], query: str, n: int = 100) -> List[str]:
    if not query:
        return list(items)[:n]
    # Prefer substring contains (case-insensitive) first for predictable narrowing
    q = query.lower()
    substr = [x for x in items if q in x.lower()]
    if substr:
        return substr[:n]
    # Fallback to fuzzy matching for non-substring queries
    hits = get_close_matches(query, list(items), n=n, cutoff=0.4)
    return hits


def run_shell(cmd: str) -> int:
    try:
        return subprocess.call(cmd, shell=True)
    except KeyboardInterrupt:
        return 130
