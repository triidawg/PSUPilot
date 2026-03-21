"""
live_plot.py — Matplotlib plot embedded in CustomTkinter, with a rolling data buffer.
"""

from collections import deque
import time

import tkinter as tk
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

WINDOW_SECONDS = 60   # how many seconds of data to show


class LivePlot(tk.Frame):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("bg", "#1a1a2e")
        super().__init__(master, **kwargs)

        self._t0 = time.monotonic()
        self._times: deque[float] = deque()
        self._voltages: deque[float] = deque()
        self._currents: deque[float] = deque()

        # ---- Matplotlib figure ----
        self._fig = Figure(figsize=(5, 3), dpi=96, facecolor="#1a1a2e")
        self._ax_v = self._fig.add_subplot(211)
        self._ax_i = self._fig.add_subplot(212, sharex=self._ax_v)
        self._fig.subplots_adjust(left=0.10, right=0.99, top=0.97, bottom=0.08, hspace=0.20)

        self._line_v, = self._ax_v.plot([], [], color="#00d4ff", linewidth=1.5)
        self._line_i, = self._ax_i.plot([], [], color="#ff9900", linewidth=1.5)

        for ax, label, color in [
            (self._ax_v, "Voltage (V)", "#00d4ff"),
            (self._ax_i, "Current (A)", "#ff9900"),
        ]:
            ax.set_facecolor("#0d0d1a")
            ax.set_ylabel(label, color=color, fontsize=8)
            ax.tick_params(colors="#aaaaaa", labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor("#333355")
            ax.yaxis.label.set_color(color)

        self._ax_i.set_xlabel("Time (s)", color="#aaaaaa", fontsize=8)

        self._mpl_canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._mpl_canvas.get_tk_widget().pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_sample(self, voltage: float, current: float):
        t = time.monotonic() - self._t0
        self._times.append(t)
        self._voltages.append(voltage)
        self._currents.append(current)
        self._trim_old(t)

    def refresh(self):
        if not self._times:
            return
        ts = list(self._times)
        vs = list(self._voltages)
        cs = list(self._currents)

        self._line_v.set_data(ts, vs)
        self._line_i.set_data(ts, cs)

        t_max = ts[-1]
        t_min = max(0.0, t_max - WINDOW_SECONDS)
        self._ax_v.set_xlim(t_min, t_max + 1)
        self._ax_i.set_xlim(t_min, t_max + 1)

        if vs:
            v_lo, v_hi = min(vs), max(vs)
            pad = max((v_hi - v_lo) * 0.1, 0.5)
            self._ax_v.set_ylim(v_lo - pad, v_hi + pad)
        if cs:
            i_lo, i_hi = min(cs), max(cs)
            pad = max((i_hi - i_lo) * 0.1, 0.05)
            self._ax_i.set_ylim(i_lo - pad, i_hi + pad)

        self._mpl_canvas.draw_idle()

    def reset(self):
        self._t0 = time.monotonic()
        self._times.clear()
        self._voltages.clear()
        self._currents.clear()
        self._line_v.set_data([], [])
        self._line_i.set_data([], [])
        self._mpl_canvas.draw_idle()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trim_old(self, t_now: float):
        cutoff = t_now - WINDOW_SECONDS
        while self._times and self._times[0] < cutoff:
            self._times.popleft()
            self._voltages.popleft()
            self._currents.popleft()
