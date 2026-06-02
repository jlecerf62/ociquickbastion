import builtins

import pytest

from qb2 import tui
from qb2.types import TargetPrivateIp


def test_ensure_available_reports_missing_textual(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "textual" or name.startswith("textual."):
            raise ModuleNotFoundError("No module named 'textual'", name="textual")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(tui.TextualUnavailable, match="Install it with"):
        tui.ensure_available()


def test_ensure_available_reports_broken_textual_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "textual.app":
            raise ImportError("cannot import name ComposeResult")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(tui.TextualUnavailable, match="could not be imported correctly"):
        tui.ensure_available()


def test_format_options_marks_selected_row():
    rendered = tui._format_options("Pick one", ["first", "second"], [0, 1], 1)
    assert "  1. first" in rendered
    assert "> 2. second" in rendered
    assert "Use Up/Down and Enter" in rendered


def test_capture_log_adds_tui_log_line():
    start = len(tui._LOG_LINES)
    tui.capture_log("INFO", "hello")
    assert tui._LOG_LINES[start:] == ["[INFO] hello"]


def test_choose_private_ip_maps_selected_index(monkeypatch):
    choices = [
        TargetPrivateIp(
            ip_address="10.0.0.10",
            vnic_id="vnic-1",
            vnic_name="primary",
            subnet_id="subnet-1",
            vcn_id="vcn-1",
            display_name="primary-ip",
            is_primary=True,
        ),
        TargetPrivateIp(
            ip_address="10.0.1.10",
            vnic_id="vnic-2",
            vnic_name="secondary",
            subnet_id="subnet-2",
            vcn_id="vcn-2",
            display_name="secondary-ip",
            is_primary=False,
        ),
    ]

    monkeypatch.setattr(tui, "_run_selection", lambda title, options: 1)

    assert tui.choose_private_ip(choices) is choices[1]
