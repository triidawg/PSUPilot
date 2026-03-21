# PSUPilot

A Windows desktop application for programming and monitoring the **GW Instek PSW160-7.2** programmable DC power supply. Build and run voltage/current cycle sequences with ramp control, live monitoring, and profile save/load — all without NI-VISA.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey) ![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- **Cycle Editor** — table-based step editor with voltage, current limit, ramp time, and dwell time per step
- **Software Ramping** — smooth voltage ramps simulated in software (~50 sub-steps), since the PSW160 has no native ramp command
- **Loop Control** — repeat sequences 1–∞ times with a live loop counter
- **Live Monitor** — real-time voltage and current plot (matplotlib, ~2.5 Hz), plus large numeric readouts
- **Run Controls** — Run / Pause / Stop with per-step progress and dwell countdown
- **Save / Load Profiles** — cycle steps saved as human-readable `.json` files
- **Multi-PSU Driver System** — add support for new PSU models by dropping a JSON driver file into `drivers/` — no code changes needed

---

## Requirements

- Windows 10/11
- Python 3.10+
- USB → virtual COM port driver for your PSU (usually installed automatically)

Install Python dependencies:

```bash
pip install -r requirements.txt
```

**Packages:** `customtkinter`, `pyvisa-py`, `matplotlib`, `pyserial`

> **Note:** Uses `pyvisa-py` — NI-VISA is **not** required.

---

## Usage

```bash
python main.py
```

1. Select your PSU model from the dropdown (auto-scanned from `drivers/`)
2. Select the COM port your PSU is connected to and click **Connect**
3. Build your cycle in the editor, set loop count, and hit **▶ Run**
4. Save/load cycle profiles via the buttons at the bottom

---

## Adding a New PSU

Create a JSON file in the `drivers/` folder named after the model code (e.g. `PSW80-13.5.json`):

```json
{
  "model": "PSW80-13.5",
  "brand": "GW Instek",
  "full_name": "GW Instek PSW80-13.5 Programmable DC Power Supply",
  "max_voltage": 80.0,
  "max_current": 13.5,
  "connection": "USB",
  "termination": "\n",
  "commands": {
    "set_voltage":     "VOLT {v}",
    "set_current":     "CURR {i}",
    "output_on":       "OUTP ON",
    "output_off":      "OUTP OFF",
    "measure_voltage": "MEAS:VOLT?",
    "measure_current": "MEAS:CURR?"
  }
}
```

The new model will appear in the PSU dropdown on next launch.

---

## File Structure

```
PSUPilot/
├── main.py              # Entry point
├── app.py               # CustomTkinter GUI
├── cycle_runner.py      # Background thread sequencer + ramp logic
├── psu_driver.py        # pyvisa-py SCPI wrapper
├── live_plot.py         # Embedded matplotlib live plot
├── drivers/
│   └── PSW160-7.2.json  # GW Instek PSW160-7.2 driver
├── profiles/            # User-saved cycle profiles (auto-created)
└── requirements.txt
```

---

## Safety

- Output is turned **OFF** automatically on Stop, error, or app close
- All voltage/current values are validated against driver limits before sending
- Communication errors are caught and displayed — the app will not crash

---

## License

MIT
