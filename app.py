"""
app.py — Main CustomTkinter application window.
"""

import csv
import json
import math
import os
import queue
import sys
import glob
import threading
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox

import customtkinter as ctk
import serial.tools.list_ports

from psu_driver import PSUDriver
from cycle_runner import ContinuousRunner, CycleRunner
from live_plot import LivePlot
import theme

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
theme.apply_theme()

# When frozen by PyInstaller the bundled files live in sys._MEIPASS;
# user data (profiles) must live next to the exe so they persist between runs.
if getattr(sys, "frozen", False):
    _RESOURCE_DIR = sys._MEIPASS
    _DATA_DIR = os.path.dirname(sys.executable)
else:
    _RESOURCE_DIR = os.path.dirname(__file__)
    _DATA_DIR = os.path.dirname(__file__)

PROFILES_DIR = os.path.join(_DATA_DIR, "profiles")
LOGS_DIR = os.path.join(_DATA_DIR, "logs")
LOG_FILE_NAME = "last_run.csv"  # overwritten by every run
DRIVERS_DIR = os.path.join(_RESOURCE_DIR, "drivers")
# User-added drivers live next to the exe/script so they survive rebuilds.
USER_DRIVERS_DIR = os.path.join(_DATA_DIR, "drivers")

os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(USER_DRIVERS_DIR, exist_ok=True)

CSV_HEADER = ["Time (s)", "Voltage (V)", "Current (A)", "Set Voltage (V)", "Set Current (A)"]

MODE_CYCLE = "Cycle Editor"
MODE_CONTINUOUS = "Continuous"

# Continuous mode voltage limits; None leaves only the PSU's own maximum
VOLTAGE_RANGES = {"0–12 V": 12.0, "0–32 V": 32.0, "0–150 V": 150.0, "Unrestricted": None}
DEFAULT_VOLTAGE_RANGE = "0–32 V"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_driver_names() -> dict[str, str]:
    """Returns {model_code: path} — e.g. {'PSW160-7.2': '/path/PSW160-7.2.json'}
    Bundled drivers are loaded first; user drivers next to the exe override them."""
    result = {}
    search_dirs = [DRIVERS_DIR, USER_DRIVERS_DIR]
    # Deduplicate in case both point to the same folder (dev mode)
    seen = set()
    for d in search_dirs:
        if d in seen:
            continue
        seen.add(d)
        for path in glob.glob(os.path.join(d, "*.json")):
            try:
                with open(path) as f:
                    data = json.load(f)
                result[data["model"]] = path
            except Exception:
                pass
    return result


def _list_com_ports() -> list[str]:
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return ports if ports else ["(none)"]


def _in_range(value: float, low: float, high: float) -> bool:
    return math.isfinite(value) and low <= value <= high


def _csv_row(t: float, v: float, i: float, set_v: float, set_i: float) -> list[str]:
    return [f"{t:.3f}", f"{v:.4f}", f"{i:.4f}", f"{set_v:.3f}", f"{set_i:.3f}"]


def _read_psu_limits(path: str) -> tuple[float, float]:
    """Returns (max_voltage, max_current) from a driver JSON file."""
    try:
        with open(path) as f:
            d = json.load(f)
        return float(d["max_voltage"]), float(d["max_current"])
    except Exception:
        return 9999.0, 9999.0


# ---------------------------------------------------------------------------
# Step row widget
# ---------------------------------------------------------------------------

class StepRow(ctk.CTkFrame):
    COLS = ["#", "Voltage (V)", "Current (A)", "Ramp (s)", "Dwell (s)"]
    WIDTHS = [24, 62, 62, 54, 54]

    # Class-level PSU limits — updated by App when the PSU selection changes
    _cls_max_voltage: float = 9999.0
    _cls_max_current: float = 9999.0

    # Optional callback(msg: str) to surface validation warnings in the editor
    _cls_warning_callback = None
    # Optional callback(row) called when a row is clicked/focused
    _cls_select_callback = None

    @classmethod
    def set_psu_limits(cls, max_v: float, max_i: float):
        cls._cls_max_voltage = max_v
        cls._cls_max_current = max_i

    @classmethod
    def set_warning_callback(cls, cb):
        cls._cls_warning_callback = cb

    @classmethod
    def set_select_callback(cls, cb):
        cls._cls_select_callback = cb

    def __init__(self, master, index: int, **kwargs):
        super().__init__(master, fg_color="transparent", corner_radius=theme.CONTROL_RADIUS, **kwargs)
        self._selected = False
        self._running = False  # this step is executing
        self._locked = False
        self._voltage_warning = ""
        self._current_warning = ""
        self._index_label = ctk.CTkLabel(self, text=str(index + 1), width=self.WIDTHS[0],
                                          anchor="center", font=("Consolas", 12))
        self._index_label.grid(row=0, column=0, padx=(theme.CONTROL_RADIUS, 1), pady=2)  # clear of the corners
        self._index_label.bind("<Button-1>", self._on_click)
        self.bind("<Button-1>", self._on_click)

        self._entries: list[ctk.CTkEntry] = []
        # Default current is "MAX" so it auto-fills from the PSU profile
        defaults = ["12.0", "MAX", "2.0", "10.0"]
        for col, (w, val) in enumerate(zip(self.WIDTHS[1:], defaults), start=1):
            e = ctk.CTkEntry(self, width=w, justify="center", font=("Consolas", 12))
            e.insert(0, val)
            e.grid(row=0, column=col, padx=1, pady=2)
            self._entries.append(e)
        self._entry_text_color = self._entries[0].cget("text_color")

        self._setup_validation()

    def _on_click(self, _event=None):
        if StepRow._cls_select_callback:
            StepRow._cls_select_callback(self)

    def select(self, active: bool):
        self._selected = active
        self._update_background()

    def _update_background(self):
        if self._running:
            self.configure(fg_color="#1a3a5c")
        elif self._selected and not self._locked:  # no selection shown while the editor is locked
            self.configure(fg_color=theme.SELECTED)
        else:
            self.configure(fg_color="transparent")

    def set_locked(self, locked: bool):
        self._locked = locked
        self._update_background()
        for e in self._entries:
            e.configure(state="disabled" if locked else "normal",
                        text_color="#888888" if locked else self._entry_text_color)

    def _setup_validation(self):
        self._entries[0].bind("<FocusOut>", self._on_voltage_focusout)
        self._entries[1].bind("<FocusOut>", self._on_current_focusout)
        for e in self._entries:
            e.bind("<FocusIn>", self._on_click)

    def _on_voltage_focusout(self, _event=None):
        e = self._entries[0]
        try:
            val = float(e.get())
        except ValueError:
            return
        if val > StepRow._cls_max_voltage:
            e.configure(border_color="#ff4444")
            self._voltage_warning = f"Voltage {val} V exceeds PSU max of {StepRow._cls_max_voltage} V"
        else:
            e.configure(border_color=theme.BORDER)
            self._voltage_warning = ""
        self._notify_warning()

    def _on_current_focusout(self, _event=None):
        e = self._entries[1]
        raw = e.get().strip().upper()
        if raw == "MAX":
            e.configure(border_color=theme.BORDER)
            self._current_warning = ""
            self._notify_warning()
            return
        try:
            val = float(raw)
        except ValueError:
            return
        if val > StepRow._cls_max_current:
            e.configure(border_color="#ff4444")
            self._current_warning = f"Current {val} A exceeds PSU max of {StepRow._cls_max_current} A"
        else:
            e.configure(border_color=theme.BORDER)
            self._current_warning = ""
        self._notify_warning()

    def _notify_warning(self):
        if StepRow._cls_warning_callback:
            StepRow._cls_warning_callback()

    def warning(self) -> str:
        """This row's limit problem, or '' if it has none."""
        return self._voltage_warning or self._current_warning

    def revalidate(self):
        """Re-run border-colour validation after PSU limits change."""
        self._on_voltage_focusout()
        self._on_current_focusout()

    def set_index(self, n: int):
        self._index_label.configure(text=str(n + 1))

    def get_values(self) -> dict:
        """Return step values as floats; 'MAX' current is resolved to the PSU max."""
        keys = ["voltage", "current", "ramp", "dwell"]
        result = {}
        for k, e in zip(keys, self._entries):
            raw = e.get().strip()
            if k == "current" and raw.upper() == "MAX":
                result[k] = StepRow._cls_max_current
            else:
                result[k] = float(raw)
        return result

    def get_raw_values(self) -> dict:
        """Like get_values() but preserves 'MAX' as a string for profile saving."""
        keys = ["voltage", "current", "ramp", "dwell"]
        result = {}
        for k, e in zip(keys, self._entries):
            raw = e.get().strip()
            if k == "current" and raw.upper() == "MAX":
                result[k] = "MAX"
            else:
                result[k] = float(raw)
        return result

    def set_values(self, d: dict):
        keys = ["voltage", "current", "ramp", "dwell"]
        for key, e in zip(keys, self._entries):
            val = d.get(key, "0")
            e.delete(0, "end")
            if key == "current" and str(val).upper() == "MAX":
                e.insert(0, "MAX")
            else:
                e.insert(0, str(val))

    def highlight(self, active: bool):
        self._running = active
        self._update_background()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PSUPilot")
        self.geometry("1200x720")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._driver: PSUDriver | None = None
        self._runner: CycleRunner | None = None
        self._driver_map = _load_driver_names()

        # Pending measurement from runner thread
        self._pending_samples: deque[tuple[float, float, float, float, float]] = deque()
        self._measure_lock = threading.Lock()
        self._plot_started = False

        # UI work requested by the runner thread. It must never wait for the window,
        # or a busy window would delay voltage changes and stretch dips.
        self._ui_events: queue.SimpleQueue = queue.SimpleQueue()

        self._selected_row: StepRow | None = None
        self._highlighted_row: StepRow | None = None

        self._run_mode = MODE_CYCLE
        self._running = False
        self._after_stop = None   # runs once the current run has fully ended
        self._log_file = None     # CSV log of the current run
        self._log_writer = None

        self._build_ui()

        # Load limits for the default-selected PSU and wire up callbacks
        self._load_psu_limits(self._psu_var.get())
        StepRow.set_warning_callback(self._show_editor_warning)
        StepRow.set_select_callback(self._on_row_selected)
        self._selected_idx: int = 0

        self._plot_refresh_loop()
        self._ui_event_loop()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self._build_toolbar()
        self._build_editor()
        self._build_monitor()
        self._build_controls()

    # --- Toolbar ---
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, height=48, border_width=1)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 0))
        bar.pack_propagate(False)  # keep the height so the controls sit clear of the corners

        ctk.CTkLabel(bar, text="PSU:").pack(side="left", padx=(12, 4))
        driver_names = list(self._driver_map.keys()) or ["(no drivers)"]
        self._psu_var = ctk.StringVar(value=driver_names[0])
        self._psu_combo = ctk.CTkComboBox(bar, values=driver_names,
                                           variable=self._psu_var, width=220,
                                           command=self._on_psu_changed)
        self._psu_combo.pack(side="left", padx=4)

        ctk.CTkLabel(bar, text="COM:").pack(side="left", padx=(16, 4))
        ports = _list_com_ports()
        self._port_var = ctk.StringVar(value=ports[0])
        self._port_combo = ctk.CTkComboBox(bar, values=ports,
                                            variable=self._port_var, width=100)
        self._port_combo.pack(side="left", padx=4)

        self._refresh_btn = ctk.CTkButton(bar, text="⟳", width=32,
                                           command=self._refresh_ports)
        self._refresh_btn.pack(side="left", padx=2)

        self._connect_btn = ctk.CTkButton(bar, text="Connect", width=90,
                                           command=self._toggle_connect)
        self._connect_btn.pack(side="left", padx=(8, 4))

        self._status_label = ctk.CTkLabel(bar, text="Disconnected",
                                           text_color="#ff5555")
        self._status_label.pack(side="left", padx=8)

        # PSU limits display — updated whenever the PSU combo changes
        self._psu_limits_label = ctk.CTkLabel(bar, text="",
                                               text_color="#888888",
                                               font=("Consolas", 11))
        self._psu_limits_label.pack(side="left", padx=(16, 4))

        self._mode = "cv"  # always CVHS — controlled by UI ramp logic

    # --- Cycle editor ---
    def _build_editor(self):
        frame = ctk.CTkFrame(self, border_width=1)
        frame.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)
        frame.grid_columnconfigure(0, weight=1, minsize=348)  # keep width when panels swap
        frame.grid_rowconfigure(1, weight=1)

        self._mode_selector = ctk.CTkSegmentedButton(frame, values=[MODE_CYCLE, MODE_CONTINUOUS],
                                                     command=self._set_run_mode)
        self._mode_selector.set(MODE_CYCLE)
        self._mode_selector.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))

        panel = ctk.CTkFrame(frame, fg_color="transparent")
        panel.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        panel.grid_columnconfigure(0, weight=1)
        self._cycle_panel = panel

        # Column headers
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.grid(row=1, column=0, sticky="ew", padx=4)
        for col, (label, w) in enumerate(zip(StepRow.COLS, StepRow.WIDTHS)):
            ctk.CTkLabel(hdr, text=label, width=w, anchor="center",
                         font=("", 11, "bold"), text_color="#888888").grid(
                row=0, column=col, padx=(theme.CONTROL_RADIUS + 1, 1) if col == 0 else 1)

        # Scrollable step list
        self._step_scroll = ctk.CTkScrollableFrame(panel, width=290, height=320, fg_color=theme.PANEL,
                                                   scrollbar_button_color=theme.CONTROL_HOVER)
        self._step_scroll.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        panel.grid_rowconfigure(2, weight=1)

        self._step_rows: list[StepRow] = []
        # Add two default steps
        self._add_step()
        self._add_step()
        self._step_rows[1].set_values({"voltage": 0.0, "current": "MAX",
                                        "ramp": 1.0, "dwell": 5.0})
        self._select_row(0)

        # Buttons
        btn_bar = ctk.CTkFrame(panel, fg_color="transparent")
        btn_bar.grid(row=3, column=0, sticky="w", padx=4, pady=(0, 2))
        self._step_btns = [
            ctk.CTkButton(btn_bar, text="+ Add", width=70, command=self._add_step),
            ctk.CTkButton(btn_bar, text="Remove", width=70, command=self._remove_step),
            ctk.CTkButton(btn_bar, text="↑", width=36, command=self._move_up),
            ctk.CTkButton(btn_bar, text="↓", width=36, command=self._move_down),
        ]
        for btn in self._step_btns:
            btn.pack(side="left", padx=2)

        # Validation warning label
        self._editor_warning_label = ctk.CTkLabel(
            panel, text="", text_color="#ff4444",
            font=("Consolas", 11), anchor="w")
        self._editor_warning_label.grid(row=4, column=0, sticky="ew",
                                         padx=8, pady=(0, 4))

        self._build_continuous(frame)

    # --- Continuous mode ---
    def _build_continuous(self, frame):
        panel = ctk.CTkFrame(frame, fg_color="transparent")
        panel.grid(row=1, column=0, sticky="nsew", padx=12, pady=(8, 6))
        panel.grid_remove()  # Cycle Editor is shown first
        self._continuous_panel = panel

        self._cont_range_selector = ctk.CTkSegmentedButton(panel, values=list(VOLTAGE_RANGES))
        self._cont_range_selector.set(DEFAULT_VOLTAGE_RANGE)
        self._cont_range_selector.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ctk.CTkLabel(panel, text="Voltage (V)", width=90, anchor="w").grid(
            row=1, column=0, sticky="w", pady=4)
        self._cont_voltage_entry = ctk.CTkEntry(panel, width=90, justify="center",
                                                font=("Consolas", 12))
        self._cont_voltage_entry.insert(0, "24.0")
        self._cont_voltage_entry.grid(row=1, column=1, sticky="w", pady=4)

        ctk.CTkLabel(panel, text="Current (A)", width=90, anchor="w").grid(
            row=2, column=0, sticky="w", pady=4)
        self._cont_current_entry = ctk.CTkEntry(panel, width=90, justify="center",
                                                font=("Consolas", 12))
        self._cont_current_entry.insert(0, "MAX")
        self._cont_current_entry.grid(row=2, column=1, sticky="w", pady=4)

        self._cont_set_btn = ctk.CTkButton(panel, text="Set", width=90,
                                           command=self._apply_continuous, state="disabled")
        self._cont_set_btn.grid(row=3, column=1, sticky="w", pady=4)
        for entry in (self._cont_voltage_entry, self._cont_current_entry):
            entry.bind("<Return>", self._apply_continuous)

        self._cont_active_label = ctk.CTkLabel(panel, text="Output: off", anchor="w",
                                               font=("Consolas", 12))
        self._cont_active_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 2))

        ctk.CTkLabel(panel, text="Holds the voltage until Stop. Press Set or\n"
                                 "Enter to change the output during the run.",
                     text_color="#888888", font=("", 11), justify="left", anchor="w").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _set_run_mode(self, mode: str):
        self._run_mode = mode
        if mode == MODE_CONTINUOUS:
            self._cycle_panel.grid_remove()
            self._continuous_panel.grid()
        else:
            self._continuous_panel.grid_remove()
            self._cycle_panel.grid()
        self._update_cycle_controls()

    def _update_cycle_controls(self):
        """Cycle settings can only be edited in Cycle Editor mode with nothing running."""
        cycle_mode = self._run_mode == MODE_CYCLE
        editable = cycle_mode and not self._running
        state = "normal" if editable else "disabled"

        for row in self._step_rows:
            row.set_locked(not editable)
        for btn in self._step_btns:
            btn.configure(state=state)
        self._load_btn.configure(state=state)
        self._save_btn.configure(state="normal" if cycle_mode else "disabled")

        colors = self._loops_colors
        self._loops_entry.configure(state=state,
                                    text_color=colors["text"] if editable else "#666666",
                                    fg_color=colors["fg"] if editable else theme.PANEL,
                                    border_color=colors["border"] if editable else theme.BORDER)
        self._loops_label.configure(text_color=colors["label"] if editable else "#666666")
        self._loops_hint.configure(text_color="#888888" if editable else "#666666")

    # --- Live monitor ---
    def _build_monitor(self):
        frame = ctk.CTkFrame(self, border_width=1)
        frame.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr, text="LIVE MONITOR", font=("", 13, "bold")).grid(
            row=0, column=0, sticky="w")

        self._full_btn = ctk.CTkButton(hdr, text="Full", width=65,
                                        fg_color="#1a5c8a", hover_color="#1e6fa8",
                                        command=lambda: self._set_graph_mode("full"))
        self._full_btn.grid(row=0, column=1, padx=(4, 2))
        self._sweep_btn = ctk.CTkButton(hdr, text="Sweep", width=65,
                                         fg_color=theme.CONTROL, hover_color=theme.CONTROL_HOVER,
                                         command=lambda: self._set_graph_mode("sweep"))
        self._sweep_btn.grid(row=0, column=2, padx=(2, 4))
        ctk.CTkButton(hdr, text="Export CSV", width=90,
                      fg_color="#2d4a2d", hover_color="#3a6a3a",
                      command=self._export_csv).grid(row=0, column=3, padx=(4, 0))

        self._plot = LivePlot(frame)
        self._plot.grid(row=1, column=0, sticky="nsew", padx=10, pady=2)

        readout = ctk.CTkFrame(frame, fg_color="transparent")
        readout.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

        self._v_label = ctk.CTkLabel(readout, text="V:  ---  V",
                                      font=("Consolas", 22, "bold"),
                                      text_color="#00d4ff")
        self._v_label.pack(anchor="w")
        self._i_label = ctk.CTkLabel(readout, text="I:  ---  A",
                                      font=("Consolas", 22, "bold"),
                                      text_color="#ff9900")
        self._i_label.pack(anchor="w")

    # --- Controls bar ---
    def _build_controls(self):
        bar = ctk.CTkFrame(self, height=62, border_width=1)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

        # Left side: two rows, both left-aligned
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", padx=12, pady=6)

        # Row 1 — loops group, tightly packed
        loops_row = ctk.CTkFrame(left, fg_color="transparent")
        loops_row.pack(anchor="w")
        self._loops_label = ctk.CTkLabel(loops_row, text="Loops:")
        self._loops_label.pack(side="left", padx=(0, 8))
        self._loops_entry = ctk.CTkEntry(loops_row, width=52, justify="center")
        self._loops_entry.insert(0, "1")
        self._loops_entry.pack(side="left")
        self._loops_hint = ctk.CTkLabel(loops_row, text="(0 = ∞)", text_color="#888888",
                                        font=("", 11))
        self._loops_hint.pack(side="left", padx=(10, 0))
        # Normal colours, restored when leaving Continuous
        self._loops_colors = {
            "label": self._loops_label.cget("text_color"),
            "text": self._loops_entry.cget("text_color"),
            "fg": self._loops_entry.cget("fg_color"),
            "border": self._loops_entry.cget("border_color"),
        }

        # Row 2 — fixed-width progress labels, locked so nothing shifts
        prog_row = ctk.CTkFrame(left, fg_color="transparent")
        prog_row.pack(anchor="w", pady=(3, 0))
        self._step_label  = ctk.CTkLabel(prog_row, text="Step: –/–",  width=90,
                                          anchor="w", font=("Consolas", 12))
        self._loop_label  = ctk.CTkLabel(prog_row, text="Loop: –/–",  width=90,
                                          anchor="w", font=("Consolas", 12))
        self._dwell_label = ctk.CTkLabel(prog_row, text="Dwell: –",   width=140,
                                          anchor="w", font=("Consolas", 12))
        self._step_label.pack(side="left", padx=(0, 10))
        self._loop_label.pack(side="left", padx=(0, 10))
        self._dwell_label.pack(side="left")

        # Middle: run controls
        mid = ctk.CTkFrame(bar, fg_color="transparent")
        mid.pack(side="left", padx=8, pady=8)

        self._run_btn = ctk.CTkButton(mid, text="▶ Run", width=90,
                                       fg_color="#2d6a2d", hover_color="#3a8a3a",
                                       command=self._run)
        self._run_btn.grid(row=0, column=0, padx=4)

        self._stop_btn = ctk.CTkButton(mid, text="⏹ Stop", width=90,
                                        fg_color="#6a1a1a", hover_color="#8a2a2a",
                                        command=self._stop, state="disabled")
        self._stop_btn.grid(row=0, column=1, padx=4)

        # Right: save/load
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=12, pady=8)

        self._save_btn = ctk.CTkButton(right, text="💾 Save Profile", width=130,
                                       command=self._save_profile)
        self._save_btn.pack(pady=2)
        self._load_btn = ctk.CTkButton(right, text="📂 Load Profile", width=130,
                                       command=self._load_profile)
        self._load_btn.pack(pady=2)

    # -----------------------------------------------------------------------
    # Port helpers
    # -----------------------------------------------------------------------

    def _refresh_ports(self):
        ports = _list_com_ports()
        self._port_combo.configure(values=ports)
        self._port_var.set(ports[0])

    def _set_graph_mode(self, mode: str):
        self._plot.set_mode(mode)
        active   = "#1a5c8a"
        inactive = theme.CONTROL
        active_h   = "#1e6fa8"
        inactive_h = theme.CONTROL_HOVER
        self._sweep_btn.configure(
            fg_color=active   if mode == "sweep" else inactive,
            hover_color=active_h if mode == "sweep" else inactive_h)
        self._full_btn.configure(
            fg_color=active   if mode == "full" else inactive,
            hover_color=active_h if mode == "full" else inactive_h)
        self._plot.refresh()  # redraw now — no new samples arrive once a cycle has ended

    # -----------------------------------------------------------------------
    # PSU selection / limits
    # -----------------------------------------------------------------------

    def _on_psu_changed(self, value: str):
        self._load_psu_limits(value)
        # Re-validate all existing step entries against the new limits
        for row in self._step_rows:
            row.revalidate()

    def _load_psu_limits(self, psu_name: str):
        path = self._driver_map.get(psu_name)
        if path:
            max_v, max_i = _read_psu_limits(path)
        else:
            max_v, max_i = 9999.0, 9999.0
        StepRow.set_psu_limits(max_v, max_i)
        # Update toolbar label
        v_str = f"{max_v:.1f}" if max_v < 9999 else "—"
        i_str = f"{max_i:.1f}" if max_i < 9999 else "—"
        self._psu_limits_label.configure(text=f"Max: {v_str} V / {i_str} A")

    # -----------------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------------

    def _toggle_connect(self):
        if self._driver and self._driver.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        driver_name = self._psu_var.get()
        path = self._driver_map.get(driver_name)
        if not path:
            messagebox.showerror("Error", "No driver found for selected PSU.")
            return
        port = self._port_var.get()
        if port == "(none)":
            messagebox.showerror("Error", "No COM port selected.")
            return
        try:
            self._driver = PSUDriver(path)
            self._driver.connect(port)
            # Identity check — abort before sending any command if the PSU
            # doesn't match the selected driver
            match, idn = self._driver.verify_idn()
            if not match:
                self._driver.close()
                self._driver = None
                messagebox.showerror(
                    "PSU Mismatch",
                    f"Selected driver: {driver_name}\n"
                    f"PSU responded:   {idn}\n\n"
                    "Connection refused — select the correct driver and try again."
                )
                return
            self._driver.set_mode(self._mode)
            self._status_label.configure(
                text=f"{self._driver.full_name}  |  {port}",
                text_color="#55ff55")
            self._connect_btn.configure(text="Disconnect")
            self._psu_combo.configure(state="disabled")
        except Exception as exc:
            if self._driver:
                self._driver.close()
            self._driver = None
            messagebox.showerror("Connection Error", str(exc))

    def _disconnect(self):
        if self._runner and self._runner.is_running():
            # Let the run turn the output off first; it disconnects when finished
            self._after_stop = self._disconnect
            self._stop()
            return
        if self._driver:
            self._driver.disconnect()
            self._driver = None
        self._status_label.configure(text="Disconnected", text_color="#ff5555")
        self._connect_btn.configure(text="Connect")
        self._psu_combo.configure(state="normal")

    # -----------------------------------------------------------------------
    # Step editor operations
    # -----------------------------------------------------------------------

    def _add_step(self):
        row = StepRow(self._step_scroll, index=len(self._step_rows))
        row.pack(fill="x", pady=1)
        self._step_rows.append(row)
        self._select_row(len(self._step_rows) - 1)

    def _remove_step(self):
        if len(self._step_rows) <= 1:
            return
        idx = self._selected_idx
        row = self._step_rows.pop(idx)
        row.destroy()
        # Re-number remaining rows
        for i, r in enumerate(self._step_rows):
            r.set_index(i)
        new_idx = min(idx, len(self._step_rows) - 1)
        self._select_row(new_idx)
        self._show_editor_warning()

    def _move_up(self):
        idx = self._selected_idx
        if idx <= 0 or len(self._step_rows) < 2:
            return
        if self._swap_step_values(idx, idx - 1):
            self._select_row(idx - 1)

    def _move_down(self):
        idx = self._selected_idx
        if idx >= len(self._step_rows) - 1 or len(self._step_rows) < 2:
            return
        if self._swap_step_values(idx, idx + 1):
            self._select_row(idx + 1)

    def _swap_step_values(self, a: int, b: int) -> bool:
        values = {}
        for idx in (a, b):
            try:
                values[idx] = self._step_rows[idx].get_raw_values()
            except ValueError as exc:
                messagebox.showerror("Invalid input", f"Step {idx + 1}: {exc}")
                return False
        self._step_rows[a].set_values(values[b])
        self._step_rows[b].set_values(values[a])
        self._step_rows[a].revalidate()
        self._step_rows[b].revalidate()
        return True

    def _on_row_selected(self, row: StepRow):
        if row in self._step_rows:
            self._select_row(self._step_rows.index(row))

    def _select_row(self, idx: int):
        self._selected_idx = idx
        if self._selected_row in self._step_rows:
            self._selected_row.select(False)
        self._selected_row = self._step_rows[idx]
        self._selected_row.select(True)

    def _show_editor_warning(self):
        msg = next((row.warning() for row in self._step_rows if row.warning()), "")
        self._editor_warning_label.configure(text=msg)

    def _get_steps(self) -> list[dict] | None:
        if not self._step_rows:
            messagebox.showerror("Invalid input", "The cycle has no steps.")
            return None
        steps = []
        max_v = StepRow._cls_max_voltage
        max_i = StepRow._cls_max_current
        for idx, row in enumerate(self._step_rows):
            try:
                s = row.get_values()
            except ValueError as exc:
                messagebox.showerror("Invalid input", f"Step {idx + 1}: {exc}")
                return None
            if not _in_range(s["voltage"], 0.0, max_v):
                messagebox.showerror("Out of range",
                                     f"Step {idx+1}: voltage {s['voltage']} V is outside "
                                     f"the PSU range of 0–{max_v} V")
                return None
            if not _in_range(s["current"], 0.0, max_i):
                messagebox.showerror("Out of range",
                                     f"Step {idx+1}: current {s['current']} A is outside "
                                     f"the PSU range of 0–{max_i} A")
                return None
            for key in ("ramp", "dwell"):
                if not math.isfinite(s[key]) or s[key] < 0:
                    messagebox.showerror("Out of range",
                                         f"Step {idx+1}: {key} time {s[key]} s is invalid")
                    return None
            steps.append(s)
        return steps

    def _get_loops(self) -> int | None:
        try:
            loops = int(self._loops_entry.get())
        except ValueError:
            loops = -1
        if loops < 0:
            messagebox.showerror("Error", "Invalid loop count.")
            return None
        return loops

    # -----------------------------------------------------------------------
    # Run / Stop
    # -----------------------------------------------------------------------

    def _run(self):
        if not self._driver or not self._driver.connected:
            messagebox.showerror("Error", "Not connected to PSU.")
            return
        if self._run_mode == MODE_CONTINUOUS:
            self._run_continuous()
        else:
            self._run_cycle()

    def _run_cycle(self):
        steps = self._get_steps()
        if steps is None:
            return
        loops = self._get_loops()
        if loops is None:
            return
        if not self._prepare_run():
            return
        self._total_steps = len(steps)
        self._total_loops = loops

        self._runner = CycleRunner(
            psu=self._driver,
            steps=steps,
            loops=loops,
            on_step=self._cb_step,
            on_dwell_tick=self._cb_dwell,
            on_measure=self._cb_measure,
            on_loop_complete=self._cb_loop,
            on_done=self._cb_done,
            on_error=self._cb_error,
        )
        self._runner.start()

    def _run_continuous(self):
        target = self._get_continuous_target()
        if target is None or not self._prepare_run():
            return
        voltage, current = target
        self._show_continuous_output(voltage, current)

        self._runner = ContinuousRunner(
            self._driver, voltage, current,
            on_dwell_tick=self._cb_elapsed,
            on_measure=self._cb_measure,
            on_done=self._cb_done,
            on_error=self._cb_error,
        )
        self._runner.start()

    def _prepare_run(self) -> bool:
        try:
            self._driver.set_mode(self._mode)  # re-apply selected mode before run
        except Exception as exc:
            messagebox.showerror("PSU Error", str(exc))
            return False

        self._plot.reset()       # clear previous run data
        self._plot_started = False  # block samples until first dwell begins
        with self._measure_lock:
            self._pending_samples.clear()
        self._open_log()
        self._set_run_state(True)
        return True

    def _get_continuous_target(self) -> tuple[float, float] | None:
        max_v = StepRow._cls_max_voltage
        max_i = StepRow._cls_max_current
        try:
            voltage = float(self._cont_voltage_entry.get())
            raw_current = self._cont_current_entry.get().strip().upper()
            current = max_i if raw_current == "MAX" else float(raw_current)
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc))
            return None
        if not _in_range(voltage, 0.0, max_v):
            messagebox.showerror("Out of range",
                                 f"Voltage {voltage} V is outside the PSU range of 0–{max_v} V")
            return None
        range_name = self._cont_range_selector.get()
        limit = VOLTAGE_RANGES[range_name]
        if limit is not None and voltage > limit:
            messagebox.showerror("Out of range",
                                 f"Voltage {voltage} V is above the selected {range_name} range.")
            return None
        if not _in_range(current, 0.0, max_i):
            messagebox.showerror("Out of range",
                                 f"Current {current} A is outside the PSU range of 0–{max_i} A")
            return None
        return voltage, current

    def _apply_continuous(self, _event=None):
        if not isinstance(self._runner, ContinuousRunner):
            return
        target = self._get_continuous_target()
        if target is None:
            return
        self._runner.set_target(*target)
        self._show_continuous_output(*target)

    def _show_continuous_output(self, voltage: float, current: float):
        self._cont_active_label.configure(text=f"Output: {voltage:.3f} V / {current:.3f} A limit")

    def _stop(self):
        if self._runner:
            self._runner.stop()

    def _set_run_state(self, running: bool):
        self._running = running
        self._update_cycle_controls()
        self._run_btn.configure(state="disabled" if running else "normal")
        self._stop_btn.configure(state="normal" if running else "disabled")
        self._cont_set_btn.configure(state="normal" if running else "disabled")
        self._mode_selector.configure(state="disabled" if running else "normal")
        self._cont_range_selector.configure(state="disabled" if running else "normal")

    # -----------------------------------------------------------------------
    # Runner callbacks (called from background thread → queued for the UI thread)
    # -----------------------------------------------------------------------

    def _cb_step(self, step_idx: int, loop_idx: int):
        self._ui_events.put(lambda: self._update_progress(step_idx, loop_idx, None))
        self._ui_events.put(lambda: self._highlight_step(step_idx))

    def _cb_dwell(self, remaining: float):
        self._start_plot()
        self._ui_events.put(lambda: self._update_dwell(remaining))

    def _cb_elapsed(self, elapsed: float):
        self._start_plot()
        self._ui_events.put(lambda: self._update_elapsed(elapsed))

    def _start_plot(self):
        if not self._plot_started:
            with self._measure_lock:
                self._pending_samples.clear()  # discard ramp samples
                self._plot_started = True      # set inside lock — atomic with the flush

    def _cb_measure(self, t: float, v: float, i: float, set_v: float, set_i: float):
        with self._measure_lock:
            self._pending_samples.append((t, v, i, set_v, set_i))

    def _cb_loop(self, loop_idx: int):
        pass  # progress updates already handled

    def _cb_done(self):
        self._ui_events.put(self._on_run_finished)

    def _cb_error(self, msg: str):
        self._ui_events.put(lambda: messagebox.showerror("PSU Error", msg))

    def _ui_event_loop(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except queue.Empty:
                break
            event()
        self.after(50, self._ui_event_loop)

    # -----------------------------------------------------------------------
    # GUI updates from callbacks
    # -----------------------------------------------------------------------

    def _update_progress(self, step_idx: int, loop_idx: int, dwell_remaining):
        n_steps = self._total_steps
        n_loops = self._total_loops
        self._step_label.configure(text=f"Step: {step_idx + 1}/{n_steps}")
        self._loop_label.configure(text=f"Loop: {loop_idx}/{'∞' if n_loops == 0 else n_loops}")
        if dwell_remaining is not None:
            self._dwell_label.configure(text=f"Dwell: {dwell_remaining:.0f}s left")

    def _update_dwell(self, remaining: float):
        self._dwell_label.configure(text=f"Dwell: {remaining:.0f}s left")

    def _update_elapsed(self, elapsed: float):
        minutes, seconds = divmod(int(elapsed), 60)
        hours, minutes = divmod(minutes, 60)
        self._dwell_label.configure(text=f"Time: {hours}:{minutes:02}:{seconds:02}")

    def _highlight_step(self, active_idx: int | None):
        if self._highlighted_row in self._step_rows:
            self._highlighted_row.highlight(False)
        self._highlighted_row = None if active_idx is None else self._step_rows[active_idx]
        if self._highlighted_row:
            self._highlighted_row.highlight(True)

    def _on_run_finished(self):
        self._runner = None
        self._drain_samples()
        self._close_log()
        self._set_run_state(False)
        self._step_label.configure(text="Step: –/–")
        self._loop_label.configure(text="Loop: –/–")
        self._dwell_label.configure(text="Dwell: –")
        self._cont_active_label.configure(text="Output: off")
        self._highlight_step(None)
        if self._after_stop:
            action, self._after_stop = self._after_stop, None
            action()

    # -----------------------------------------------------------------------
    # Plot refresh loop (runs on main thread via after)
    # -----------------------------------------------------------------------

    def _plot_refresh_loop(self):
        self._drain_samples()
        self.after(400, self._plot_refresh_loop)

    def _drain_samples(self):
        with self._measure_lock:
            samples = list(self._pending_samples) if self._plot_started else []
            self._pending_samples.clear()

        if not samples:
            return
        for t, v, i, set_v, set_i in samples:
            elapsed = self._plot.add_sample(t, v, i, set_v, set_i)
            if self._log_writer:
                self._log_writer.writerow(_csv_row(elapsed, v, i, set_v, set_i))
        if self._log_file:
            self._log_file.flush()
        self._v_label.configure(text=f"V:  {v:7.3f}  V")
        self._i_label.configure(text=f"I:  {i:7.3f}  A")
        self._plot.refresh()

    def _open_log(self):
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            self._log_file = open(os.path.join(LOGS_DIR, LOG_FILE_NAME), "w", newline="")
            self._log_writer = csv.writer(self._log_file)
            self._log_writer.writerow(CSV_HEADER)
        except Exception as exc:
            self._close_log()
            messagebox.showwarning("Log Error",
                                   f"This run will not be saved to {LOG_FILE_NAME}. "
                                   f"Is it open in another program?\n\n{exc}")

    def _close_log(self):
        if self._log_file:
            self._log_file.close()
        self._log_file = None
        self._log_writer = None

    # -----------------------------------------------------------------------
    # Save / Load profiles
    # -----------------------------------------------------------------------

    def _export_csv(self):
        data = self._plot.get_data()
        if not data[0]:
            messagebox.showinfo("Export CSV", "No data to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADER)
                for row in zip(*data):
                    writer.writerow(_csv_row(*row))
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    def _save_profile(self):
        steps = []
        for idx, row in enumerate(self._step_rows):
            try:
                s = row.get_raw_values()
            except ValueError as exc:
                messagebox.showerror("Invalid input", f"Step {idx + 1}: {exc}")
                return
            steps.append(s)

        loops = self._get_loops()
        if loops is None:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")],
            initialdir=PROFILES_DIR,
        )
        if not path:
            return
        try:
            profile = {"loops": loops, "steps": steps}
            with open(path, "w") as f:
                json.dump(profile, f, indent=2)
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))

    def _load_profile(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")],
            initialdir=PROFILES_DIR,
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)

            # Support both old format (bare list) and new format (dict with loops/steps)
            if isinstance(data, list):
                steps = data
                loops = 1
            else:
                steps = data.get("steps", [])
                loops = data.get("loops", 1)

            if not steps:
                raise ValueError("Profile has no steps.")

            # Rebuild step rows
            while self._step_rows:
                self._step_rows.pop().destroy()
            for step in steps:
                self._add_step()
                self._step_rows[-1].set_values(step)
                self._step_rows[-1].revalidate()
            self._select_row(0)
            self._show_editor_warning()

            # Restore loop count
            self._loops_entry.delete(0, "end")
            self._loops_entry.insert(0, str(loops))

        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))

    # -----------------------------------------------------------------------
    # Close
    # -----------------------------------------------------------------------

    def _on_close(self):
        if self._runner and self._runner.is_running():
            # Let the run turn the output off first; it closes the window when finished
            self._after_stop = self._on_close
            self._stop()
            return
        if self._driver:
            self._driver.disconnect()
        self._close_log()
        self.destroy()
