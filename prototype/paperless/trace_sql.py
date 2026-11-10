"""Django execute_wrapper for synthetic fixtures; not a benchmark timer.

Records execute time (excluding fetch), parameter types and stack locations.
No parameter values, source lines or exception messages are recorded.
"""
import time
import traceback
from contextlib import contextmanager
from pathlib import Path


class SQLTrace:
    def __init__(self):
        self.queries = []
        self.phase = "unlabelled"

    @contextmanager
    def during(self, phase):
        previous = self.phase
        self.phase = phase
        try:
            yield
        finally:
            self.phase = previous

    def __call__(self, execute, sql, params, many, context):
        # Materializing executemany inputs would alter program behavior.
        types = None if many or params is None else [
            type(value).__name__ for value in (
                params.values() if isinstance(params, dict) else params
            )
        ]
        connection = context["connection"]
        stack = traceback.extract_stack(limit=32)[:-1]
        record = {
            "phase": self.phase,
            "sql": str(sql),
            "sql_utf8_bytes": len(str(sql).encode("utf-8")),
            "parameter_types": types,
            "many": many,
            "in_atomic_block": connection.in_atomic_block,
            "autocommit": connection.get_autocommit(),
            "stack": [
                {"file": str(Path(frame.filename)), "line": frame.lineno,
                 "function": frame.name}
                for frame in stack
            ],
        }
        started = time.perf_counter_ns()
        try:
            result = execute(sql, params, many, context)
        except BaseException as exc:
            record["error_type"] = type(exc).__name__
            raise
        else:
            return result
        finally:
            record["execute_ns"] = time.perf_counter_ns() - started
            self.queries.append(record)
