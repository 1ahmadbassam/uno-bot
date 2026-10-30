#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from collections import deque
from contextlib import contextmanager
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor


logger = logging.getLogger(__name__)

# Time the current task spent waiting on purpose (see expected_wait)
_task = threading.local()


@contextmanager
def expected_wait():
    """
    Mark a deliberate wait inside a game task, e.g. for a drand round to come
    out: it doesn't count towards the slow-task warning.
    """
    started = time.monotonic()
    try:
        yield
    finally:
        _task.waited = getattr(_task, 'waited', 0.0) + time.monotonic() - started


class GameThreadRunner:
    """Execute tasks in per-key FIFO order on a fixed-size global worker pool."""

    def __init__(self, idle_ttl_seconds=None, cleanup_every_submits=None, worker_count=None):
        self._queues = {}
        self._active_keys = set()
        self._last_used = {}
        self._pending_counts = {}
        self._lock = threading.Lock()
        self._worker_count = int(os.getenv(
            "GAME_THREAD_WORKERS",
            str(32 if worker_count is None else worker_count)
        ))
        self._executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="game-worker"
        )
        self._idle_ttl_seconds = int(os.getenv(
            "GAME_THREAD_IDLE_TTL_SECONDS",
            str(300 if idle_ttl_seconds is None else idle_ttl_seconds)
        ))
        self._cleanup_every_submits = int(os.getenv(
            "GAME_THREAD_CLEANUP_EVERY_SUBMITS",
            str(20 if cleanup_every_submits is None else cleanup_every_submits)
        ))
        self._submits = 0
        self._slow_queue_warn_seconds = float(os.getenv("GAME_THREAD_SLOW_QUEUE_WARN_SECONDS", "1.5"))
        self._slow_run_warn_seconds = float(os.getenv("GAME_THREAD_SLOW_RUN_WARN_SECONDS", "2.0"))
        self._queue_depth_warn = int(os.getenv("GAME_THREAD_QUEUE_DEPTH_WARN", "4"))

    def submit(self, key, fn, *args, **kwargs):
        key = str(key)
        submitted_at = time.monotonic()
        future = Future()
        should_schedule = False
        with self._lock:
            queue = self._queues.get(key)
            if queue is None:
                queue = deque()
                self._queues[key] = queue
            queue.append((submitted_at, fn, args, kwargs, future))
            pending_after_submit = self._pending_counts.get(key, 0) + 1
            self._pending_counts[key] = pending_after_submit
            self._last_used[key] = submitted_at

            if key not in self._active_keys:
                self._active_keys.add(key)
                should_schedule = True

            self._submits += 1
            should_cleanup = (self._submits % self._cleanup_every_submits == 0)

        if pending_after_submit >= self._queue_depth_warn:
            logger.warning("Game thread queue depth key=%s depth=%d", key, pending_after_submit)

        if should_schedule:
            self._executor.submit(self._drain_key, key)

        if should_cleanup:
            self._cleanup_idle_keys()

        future.add_done_callback(lambda f, task_key=key: self._log_if_failed(f, task_key))
        return future

    def _drain_key(self, key):
        while True:
            with self._lock:
                queue = self._queues.get(key)
                if not queue:
                    self._active_keys.discard(key)
                    self._last_used[key] = time.monotonic()
                    return
                submitted_at, fn, args, kwargs, future = queue.popleft()
                depth = self._pending_counts.get(key, 0)

            started_at = time.monotonic()
            queue_wait = started_at - submitted_at
            if queue_wait >= self._slow_queue_warn_seconds:
                logger.warning("Game thread slow queue key=%s wait=%.3fs depth=%d",
                               key, queue_wait, depth)

            _task.waited = 0.0
            try:
                result = fn(*args, **kwargs)
            except Exception as err:
                future.set_exception(err)
            else:
                future.set_result(result)
            finally:
                duration = time.monotonic() - started_at
                with self._lock:
                    pending = self._pending_counts.get(key, 0)
                    if pending <= 1:
                        self._pending_counts.pop(key, None)
                    else:
                        self._pending_counts[key] = pending - 1
                    self._last_used[key] = time.monotonic()

                waited = getattr(_task, 'waited', 0.0)
                if duration - waited >= self._slow_run_warn_seconds:
                    logger.warning("Game thread slow task key=%s run=%.3fs (%.3fs expected waiting) depth=%d",
                                   key, duration, waited, depth)

    def _cleanup_idle_keys(self):
        now = time.monotonic()
        cutoff = now - self._idle_ttl_seconds
        with self._lock:
            stale_keys = [
                key for key, ts in self._last_used.items()
                if ts < cutoff and key not in self._active_keys and not self._queues.get(key)
            ]
            for key in stale_keys:
                self._queues.pop(key, None)
                self._last_used.pop(key, None)
                self._pending_counts.pop(key, None)

    @staticmethod
    def _log_if_failed(future, key):
        err = future.exception()
        if err is None:
            return
        logger.error("Unhandled exception in game thread '%s'", key,
                     exc_info=(type(err), err, err.__traceback__))


game_thread_runner = GameThreadRunner()
