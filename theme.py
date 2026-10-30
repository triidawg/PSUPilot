"""
theme.py — Window and panel colours, and rounded corners. Button, graph and
status colours stay with the widgets that use them.
"""

import customtkinter as ctk

WINDOW = "#111518"          # behind the panels
PANEL = "#1c2023"           # panels and bars
WELL = "#0f1112"            # inside the plots
CONTROL = "#272b2e"         # entries, inactive buttons, dropdowns
CONTROL_HOVER = "#384344"
SELECTED = "#384344"        # selected row inside the step list
BORDER = "#2e3336"

PANEL_RADIUS = 14
CONTROL_RADIUS = 8


def apply_theme():
    """Override the default widget colours. Call before creating any widgets."""
    t = ctk.ThemeManager.theme

    t["CTk"]["fg_color"] = WINDOW

    t["CTkFrame"]["fg_color"] = PANEL
    t["CTkFrame"]["top_fg_color"] = WELL
    t["CTkFrame"]["border_color"] = BORDER
    t["CTkFrame"]["corner_radius"] = PANEL_RADIUS

    t["CTkButton"]["corner_radius"] = CONTROL_RADIUS

    t["CTkEntry"]["fg_color"] = CONTROL
    t["CTkEntry"]["border_color"] = BORDER
    t["CTkEntry"]["corner_radius"] = CONTROL_RADIUS

    t["CTkComboBox"]["fg_color"] = CONTROL
    t["CTkComboBox"]["border_color"] = BORDER
    t["CTkComboBox"]["button_color"] = BORDER
    t["CTkComboBox"]["button_hover_color"] = CONTROL_HOVER
    t["CTkComboBox"]["corner_radius"] = CONTROL_RADIUS

    t["DropdownMenu"]["fg_color"] = CONTROL
    t["DropdownMenu"]["hover_color"] = CONTROL_HOVER

    t["CTkScrollbar"]["button_color"] = BORDER
    t["CTkScrollbar"]["button_hover_color"] = CONTROL_HOVER

    t["CTkSegmentedButton"]["fg_color"] = CONTROL
    t["CTkSegmentedButton"]["unselected_color"] = CONTROL
    t["CTkSegmentedButton"]["unselected_hover_color"] = CONTROL_HOVER
    t["CTkSegmentedButton"]["corner_radius"] = CONTROL_RADIUS
