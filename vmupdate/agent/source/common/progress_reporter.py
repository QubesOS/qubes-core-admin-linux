import io
import os
import sys
from typing import Callable, Optional


class Progress:
    def __init__(
            self,
            weight: int,
    ):
        self.weight = weight
        self._callback = None
        self._start_percent = None
        self._stop_percent = None
        self._last_percent = None
        self._stdout = None
        self._stderr = None

    def init(
            self, start: float, stop: float,
            callback: Callable[[float], None],
            stdout: io.TextIOWrapper, stderr: io.TextIOWrapper
    ):
        self._callback = callback
        self._start_percent = start
        self._stop_percent = stop
        self._last_percent = start
        self._stdout = stdout
        self._stderr = stderr

    def notify_callback(self, percent):
        """
        Report ongoing progress.
        """
        assert self._start_percent is not None  # call init() first!
        _percent = self._start_percent + percent * (
                self._stop_percent - self._start_percent) / 100
        _percent = round(_percent, 2)
        if self._last_percent < _percent:
            self._callback(_percent)
            self._last_percent = _percent


class ProgressReporter:
    """
    Simple rough progress reporter.

    It is assumed that updating, fetching and installing
     takes fixed value of total time.
    """

    def __init__(
            self,
            update: Progress,
            fetch: Progress,
            upgrade: Progress,
            callback: Optional[Callable[[float], None]] = None
    ):
        saved_stdout = os.dup(sys.stdout.fileno())
        saved_stderr = os.dup(sys.stderr.fileno())
        self.stdout = io.TextIOWrapper(os.fdopen(saved_stdout, 'wb'))
        self.stderr = io.TextIOWrapper(os.fdopen(saved_stderr, 'wb'))
        self.last_percent = 0.0
        if callback is None:
            self.callback = lambda p: \
                print(f"{p:.2f}", flush=True, file=self.stdout)
        else:
            self.callback = callback

        total = update.weight + fetch.weight + upgrade.weight
        update_end = update.weight / total * 100
        fetch_end = fetch.weight / total * 100 + update_end

        update.init(0, update_end, self.callback, self.stdout, self.stderr)
        fetch.init(
            update_end, fetch_end, self.callback, self.stdout, self.stderr)
        upgrade.init(fetch_end, 100, self.callback, self.stdout, self.stderr)

        self.update_progress = update
        self.fetch_progress = fetch
        self.upgrade_progress = upgrade
