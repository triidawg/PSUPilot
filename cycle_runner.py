"""
cycle_runner.py — Step sequencer, software ramp logic, and loop control.
Runs entirely in a background thread so the UI stays responsive.
"""

import math
import threading
import time
from typing import Callable

RAMP_STEP_VOLTS = 0.5   # target volts per ramp sub-step
MAX_STEP_DELAY = 0.4    # seconds — voltage must advance at least this often, regardless of delta_v
MIN_STEP_DELAY = 0.02   # seconds — floor so short/large ramps don't flood the serial link

MEASURE_INTERVAL = 0.4      # seconds — ~2.5 Hz
DWELL_END_MARGIN = 0.1      # seconds — no measuring this close to a dwell's end, a query would overrun it
MAX_MEASURE_FAILURES = 3    # consecutive failed measurements before the run is aborted

SETTLE_TIMEOUT = 3.0        # seconds — give up waiting for the first step's voltage after this
SETTLE_TOLERANCE_V = 0.5    # volts — first step counts as reached within this, or
SETTLE_TOLERANCE_PCT = 0.02 # this fraction of the target, whichever is larger


class CycleRunner:
    """
    Executes a list of cycle steps on a PSUDriver instance.

    Each step dict:
        {"voltage": float, "current": float, "ramp": float, "dwell": float}

    Callbacks (all called from the runner thread — schedule GUI updates via `after`):
        on_step(step_index, loop_index)
        on_dwell_tick(seconds_remaining)
        on_measure(timestamp, voltage, current)
        on_loop_complete(loop_index)
        on_done()
        on_error(message)
    """

    def __init__(self, psu, steps: list[dict], loops: int,
                 on_step: Callable = None,
                 on_dwell_tick: Callable = None,
                 on_measure: Callable = None,
                 on_loop_complete: Callable = None,
                 on_done: Callable = None,
                 on_error: Callable = None):
        self._psu = psu
        self._steps = steps
        self._loops = loops  # 0 = infinite

        self._on_step = on_step or (lambda *a: None)
        self._on_dwell_tick = on_dwell_tick or (lambda *a: None)
        self._on_measure = on_measure or (lambda *a: None)
        self._on_loop_complete = on_loop_complete or (lambda *a: None)
        self._on_done = on_done or (lambda *a: None)
        self._on_error = on_error or (lambda *a: None)

        self._stop_event = threading.Event()

        self._next_measure = 0.0
        self._measure_failures = 0

        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal runner
    # ------------------------------------------------------------------

    def _wait_until(self, deadline: float, on_tick: Callable = None,
                    measure_margin: float = 0.0) -> bool:
        """Wait until time.monotonic() reaches deadline, measuring along the way.
        Returns False if stopped."""
        while True:
            if self._stop_event.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if on_tick:
                on_tick(remaining)
            if remaining > measure_margin:
                self._measure_if_due()
            remaining = deadline - time.monotonic()
            time.sleep(max(0, min(0.05, remaining)))

    def _run(self):
        try:
            # Always start from a safe state regardless of what the PSU was
            # left at by a previous run.
            self._psu.set_voltage(0.0)
            self._psu.set_current(0.0)
            if self._stop_event.is_set():
                return
            self._psu.output_on()
            loop = 0
            prev_voltage = 0.0  # set once only: later loops continue from the last step's voltage
            while True:
                if self._stop_event.is_set():
                    return
                loop += 1

                for step_idx, step in enumerate(self._steps):
                    if self._stop_event.is_set():
                        return

                    target_v = float(step["voltage"])
                    target_i = float(step["current"])
                    ramp_t = float(step["ramp"])
                    dwell_t = float(step["dwell"])

                    self._on_step(step_idx, loop)

                    # --- Set current limit first ---
                    self._psu.set_current(target_i)

                    # --- Ramp voltage ---
                    if ramp_t > 0:
                        delta_v = abs(target_v - prev_voltage)
                        n_by_volts = round(delta_v / RAMP_STEP_VOLTS)
                        n_by_time = math.ceil(ramp_t / MAX_STEP_DELAY)
                        n = max(1, n_by_volts, n_by_time)
                        n = min(n, max(1, int(ramp_t / MIN_STEP_DELAY)))  # don't flood the serial link
                        step_v = (target_v - prev_voltage) / n
                        step_delay = ramp_t / n
                        ramp_start = time.monotonic()
                        for i in range(1, n + 1):
                            if self._stop_event.is_set():
                                return
                            v = target_v if i == n else prev_voltage + step_v * i
                            self._psu.set_voltage(v)
                            # Timed from the ramp's start so command delays don't add up
                            if not self._wait_until(ramp_start + step_delay * i):
                                return
                    else:
                        self._psu.set_voltage(target_v)

                    prev_voltage = target_v

                    # --- First step only — the rise from 0 V isn't part of the test, so the
                    # dwell and the plot start once the PSU has arrived. Any wait on later
                    # steps would stretch dips and overvoltages. ---
                    if loop == 1 and step_idx == 0 and not self._settle(target_v):
                        return

                    # --- Dwell — timed from the voltage command, so it includes the PSU's rise time ---
                    if not self._wait_until(time.monotonic() + dwell_t,
                                            on_tick=self._on_dwell_tick,
                                            measure_margin=DWELL_END_MARGIN):
                        return

                self._on_loop_complete(loop)

                if self._loops != 0 and loop >= self._loops:
                    break

        except Exception as exc:
            self._on_error(str(exc))
        finally:
            try:
                self._psu.output_off()
            except Exception:
                pass
            # Leave the PSU at 0 so a high setpoint can't carry over to the next test
            try:
                self._psu.set_voltage(0.0)
                self._psu.set_current(0.0)
            except Exception:
                pass
            self._on_done()

    def _settle(self, target_v: float) -> bool:
        """Wait for the measured voltage to reach target_v, up to SETTLE_TIMEOUT.
        Carries on after the timeout, as a load or current limit may hold it below
        the target. Returns False if stopped."""
        tolerance = max(SETTLE_TOLERANCE_V, target_v * SETTLE_TOLERANCE_PCT)
        deadline = time.monotonic() + SETTLE_TIMEOUT
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            v = self._measure()
            if v is not None and abs(v - target_v) <= tolerance:
                return True
            time.sleep(0.05)
        return True

    def _measure_if_due(self):
        now = time.monotonic()
        if now >= self._next_measure:
            self._measure()
            self._next_measure = time.monotonic() + MEASURE_INTERVAL

    def _measure(self) -> float | None:
        """Take a measurement. Returns the voltage, or None if this attempt failed."""
        timestamp = time.monotonic()
        try:
            v = self._psu.measure_voltage()
            i = self._psu.measure_current()
        except Exception as exc:
            self._measure_failures += 1
            if self._measure_failures >= MAX_MEASURE_FAILURES:
                raise RuntimeError(f"Lost connection to PSU: {exc}") from exc
            return None
        self._measure_failures = 0
        self._on_measure(timestamp, v, i)
        return v
