"""
cycle_runner.py — Step sequencer, software ramp logic, and loop control.
Runs entirely in a background thread so the UI stays responsive.
"""

import threading
import time
from typing import Callable

RAMP_STEPS = 50  # number of sub-steps per ramp


class CycleRunner:
    """
    Executes a list of cycle steps on a PSUDriver instance.

    Each step dict:
        {"voltage": float, "current": float, "ramp": float, "dwell": float}

    Callbacks (all called from the runner thread — schedule GUI updates via `after`):
        on_step(step_index, loop_index)
        on_dwell_tick(seconds_remaining)
        on_measure(voltage, current)
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
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially

        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self):
        self._stop_event.clear()
        self._pause_event.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal runner
    # ------------------------------------------------------------------

    def _sleep(self, seconds: float) -> bool:
        """Sleep in small slices, honouring stop/pause. Returns False if stopped."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop_event.is_set():
                return False
            self._pause_event.wait()          # blocks while paused
            if self._stop_event.is_set():
                return False
            time.sleep(min(0.05, end - time.monotonic()))
        return True

    def _run(self):
        try:
            self._psu.output_on()
            loop = 0
            while True:
                loop += 1
                prev_voltage = 0.0

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
                        n = RAMP_STEPS
                        step_v = (target_v - prev_voltage) / n
                        step_delay = ramp_t / n
                        for i in range(1, n + 1):
                            if self._stop_event.is_set():
                                return
                            self._pause_event.wait()
                            v = prev_voltage + step_v * i
                            self._psu.set_voltage(v)
                            if not self._sleep(step_delay):
                                return
                            # measure during ramp at reduced rate
                            if i % 10 == 0:
                                self._measure()
                    else:
                        self._psu.set_voltage(target_v)

                    prev_voltage = target_v

                    # --- Dwell ---
                    dwell_end = time.monotonic() + dwell_t
                    measure_interval = 0.4  # ~2.5 Hz
                    next_measure = time.monotonic()
                    while time.monotonic() < dwell_end:
                        if self._stop_event.is_set():
                            return
                        self._pause_event.wait()
                        remaining = dwell_end - time.monotonic()
                        self._on_dwell_tick(max(0, remaining))
                        if time.monotonic() >= next_measure:
                            self._measure()
                            next_measure += measure_interval
                        time.sleep(0.1)

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
            self._on_done()

    def _measure(self):
        try:
            v = self._psu.measure_voltage()
            i = self._psu.measure_current()
            self._on_measure(v, i)
        except Exception:
            pass
