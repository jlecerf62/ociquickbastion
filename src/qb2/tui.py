from __future__ import annotations

import contextlib
import io
import os
import pty
import select
import shlex
import signal
import subprocess
import threading
from typing import Callable, List, Optional

from .labels import compartment_path_labels, sort_compartments_by_hierarchy, target_private_ip_label
from .types import AllowListMode, SessionDescriptor, SessionMode, TargetPrivateIp, TargetResource
from .util import fuzzy_filter, set_log_sink


class TextualUnavailable(RuntimeError):
    pass


_LOG_LINES: List[str] = []


def capture_log(level: str, msg: str) -> None:
    _LOG_LINES.append(f"[{level}] {msg}")


def _load_textual():
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Button, Footer, Header, Input, RichLog, Static
    except ModuleNotFoundError as exc:
        if exc.name != "textual" and not exc.name.startswith("textual."):
            raise
        raise TextualUnavailable(
            "Textual is required for --tui. Install it with: python -m pip install -e '.[tui]'"
        ) from exc
    except ImportError as exc:
        raise TextualUnavailable(f"Textual is installed but could not be imported correctly: {exc}") from exc
    return App, ComposeResult, Horizontal, Vertical, Button, Footer, Header, Input, RichLog, Static


def ensure_available() -> None:
    _load_textual()


def _format_log_tail(limit: int = 8) -> str:
    if not _LOG_LINES:
        return ""
    return "\n".join(_LOG_LINES[-limit:])


def _format_options(title: str, options: List[str], indexes: List[int], selected_position: int) -> str:
    if not indexes:
        return f"{title}\n\nNo matches."
    lines = [title, ""]
    for display_index, option_index in enumerate(indexes, 1):
        marker = ">" if display_index - 1 == selected_position else " "
        lines.append(f"{marker} {display_index}. {options[option_index]}")
    lines.append("")
    lines.append("Use Up/Down and Enter, type to filter, enter a number, or press Esc to cancel.")
    return "\n".join(lines)


def _run_selection(title: str, options: List[str], *, allow_cancel: bool = True) -> Optional[int]:
    if not options:
        return None

    App, ComposeResult, _Horizontal, Vertical, _Button, Footer, Header, Input, _RichLog, Static = _load_textual()

    class SelectionApp(App):
        BINDINGS = [
            ("escape", "cancel", "Cancel"),
            ("up", "cursor_up", "Up"),
            ("down", "cursor_down", "Down"),
            ("enter", "select", "Select"),
        ]
        CSS = """
        Screen { padding: 1 2; }
        #body { height: 1fr; }
        #log { height: auto; max-height: 9; border: solid $secondary; padding: 1; }
        #options { height: 1fr; border: solid $accent; padding: 1; }
        #query { dock: bottom; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.indexes = list(range(len(options)))
            self.selected_position = 0

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="body"):
                yield Static(_format_log_tail(), id="log")
                yield Static(_format_options(title, options, self.indexes, self.selected_position), id="options")
                yield Input(placeholder="number or filter", id="query")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#query", Input).focus()

        def action_cancel(self) -> None:
            self.exit(None if allow_cancel else 0)

        def _refresh(self) -> None:
            if self.indexes:
                self.selected_position = max(0, min(self.selected_position, len(self.indexes) - 1))
            else:
                self.selected_position = 0
            self.query_one("#log", Static).update(_format_log_tail())
            self.query_one("#options", Static).update(
                _format_options(title, options, self.indexes, self.selected_position)
            )

        def action_cursor_up(self) -> None:
            if self.indexes:
                self.selected_position = (self.selected_position - 1) % len(self.indexes)
                self._refresh()

        def action_cursor_down(self) -> None:
            if self.indexes:
                self.selected_position = (self.selected_position + 1) % len(self.indexes)
                self._refresh()

        def action_select(self) -> None:
            if self.indexes:
                self.exit(self.indexes[self.selected_position])

        def on_input_changed(self, event: Input.Changed) -> None:
            query = event.value.strip()
            if not query or query.isdigit():
                self.indexes = list(range(len(options)))
            else:
                matches = fuzzy_filter(options, query, n=len(options))
                self.indexes = [options.index(match) for match in matches]
            self.selected_position = 0
            self._refresh()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            value = event.value.strip()
            if not value:
                self.action_select()
                return
            if value.isdigit():
                selected = int(value) - 1
                if 0 <= selected < len(self.indexes):
                    self.exit(self.indexes[selected])
                return
            if len(self.indexes) == 1:
                self.exit(self.indexes[0])

    return SelectionApp().run()


def _run_text_input(
    title: str,
    prompt: str,
    *,
    default: str = "",
    validator: Optional[Callable[[str], bool]] = None,
    invalid_message: str = "Invalid value.",
) -> Optional[str]:
    App, ComposeResult, _Horizontal, Vertical, _Button, Footer, Header, Input, _RichLog, Static = _load_textual()

    class TextInputApp(App):
        BINDINGS = [("escape", "cancel", "Cancel")]
        CSS = """
        Screen { padding: 1 2; }
        #log { height: auto; max-height: 9; border: solid $secondary; padding: 1; }
        #box { height: auto; border: solid $accent; padding: 1; }
        #message { color: $error; min-height: 1; }
        """

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static(_format_log_tail(), id="log")
            with Vertical(id="box"):
                yield Static(title)
                yield Static(prompt)
                yield Input(value=default, id="value")
                yield Static("", id="message")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#value", Input).focus()

        def action_cancel(self) -> None:
            self.exit(None)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            value = event.value.strip() or default
            if validator is not None and not validator(value):
                self.query_one("#message", Static).update(invalid_message)
                return
            self.exit(value)

    return TextInputApp().run()


def _run_confirm(title: str, prompt: str, *, default: bool = True) -> bool:
    App, ComposeResult, Horizontal, Vertical, Button, Footer, Header, _Input, _RichLog, Static = _load_textual()

    class ConfirmApp(App):
        BINDINGS = [("escape", "cancel", "Cancel")]
        CSS = """
        Screen { padding: 1 2; }
        #log { height: auto; max-height: 9; border: solid $secondary; padding: 1; }
        #box { height: auto; border: solid $accent; padding: 1; }
        Button { margin-right: 1; }
        """

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static(_format_log_tail(), id="log")
            with Vertical(id="box"):
                yield Static(title)
                yield Static(prompt)
                with Horizontal():
                    yield Button("Yes", id="yes", variant="success")
                    yield Button("No", id="no")
            yield Footer()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.exit(event.button.id == "yes")

        def action_cancel(self) -> None:
            self.exit(default)

    return bool(ConfirmApp().run())


class _LogWriter(io.TextIOBase):
    def __init__(self, write_line: Callable[[str], None]) -> None:
        self._write_line = write_line
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._write_line(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._write_line(self._buffer)
            self._buffer = ""


def run_with_status(title: str, work: Callable[[], object]):
    App, ComposeResult, _Horizontal, Vertical, _Button, Footer, Header, _Input, RichLog, Static = _load_textual()

    class StatusApp(App):
        BINDINGS = [("ctrl+c", "cancel", "Cancel")]
        CSS = """
        Screen { padding: 1 2; }
        #log { height: 1fr; border: solid $accent; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.result = None
            self.error: Optional[BaseException] = None
            self.log_widget = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical():
                yield Static(title)
                yield RichLog(id="log", wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            self.log_widget = self.query_one("#log", RichLog)
            for line in _LOG_LINES[-8:]:
                self.log_widget.write(line)
            threading.Thread(target=self._run_work, daemon=True).start()

        def _write_line(self, line: str) -> None:
            _LOG_LINES.append(line)
            self.call_from_thread(self.log_widget.write, line)

        def _run_work(self) -> None:
            def sink(level: str, msg: str) -> None:
                self._write_line(f"[{level}] {msg}")

            writer = _LogWriter(self._write_line)
            set_log_sink(sink)
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    self.result = work()
            except BaseException as exc:
                self.error = exc
            finally:
                writer.flush()
                set_log_sink(capture_log)
                self.call_from_thread(self.exit, 0)

        def action_cancel(self) -> None:
            self.exit(130)

    app = StatusApp()
    app.run()
    if app.error is not None:
        raise app.error
    return app.result


def _valid_port(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 65535


def choose_compartment(compartments: List[dict]) -> Optional[str]:
    ordered_compartments = sort_compartments_by_hierarchy(compartments)
    labels = compartment_path_labels(ordered_compartments)
    opts = ["All compartments (current region)"] + [labels[c["id"]] for c in ordered_compartments]
    selected = _run_selection("Choose a compartment", opts)
    if selected is None:
        return ""
    if selected == 0:
        return None
    return ordered_compartments[selected - 1]["id"]


def choose_target(resources: List[TargetResource]) -> Optional[TargetResource]:
    if not resources:
        return None

    def label(r: TargetResource) -> str:
        return f"{r.name} | {r.private_ip} | {r.resource_type.value} | {r.compartment_name}"

    selected = _run_selection("Choose a target", [label(r) for r in resources])
    if selected is None:
        return None
    return resources[selected]


def choose_private_ip(private_ips: List[TargetPrivateIp]) -> Optional[TargetPrivateIp]:
    if not private_ips:
        return None
    selected = _run_selection("Choose target private IP", [target_private_ip_label(private_ip) for private_ip in private_ips])
    if selected is None:
        return None
    return private_ips[selected]


def choose_mode(default: SessionMode = SessionMode.PFWD) -> SessionMode:
    opts = [
        "Port-forwarding (default)",
        "Managed SSH",
        "SOCKS (dynamic port forwarding)",
    ]
    selected = _run_selection("Choose session mode", opts)
    if selected is None:
        return default
    return [SessionMode.PFWD, SessionMode.SSH, SessionMode.SOCKS][selected]


def choose_ports(mode: SessionMode, default_local_port: Optional[int] = None) -> tuple[Optional[int], Optional[int]]:
    if mode == SessionMode.PFWD:
        local_default = str(default_local_port if default_local_port is not None else 4444)
        local = _run_text_input(
            "Port-forwarding",
            "Local port",
            default=local_default,
            validator=_valid_port,
            invalid_message="Enter a port between 1 and 65535.",
        )
        if local is None:
            return None, None
        remote = _run_text_input(
            "Port-forwarding",
            "Remote port",
            default="22",
            validator=_valid_port,
            invalid_message="Enter a port between 1 and 65535.",
        )
        if remote is None:
            return None, None
        return int(local), int(remote)

    if mode == SessionMode.SOCKS:
        local_default = str(default_local_port if default_local_port is not None else 3128)
        local = _run_text_input(
            "SOCKS",
            "Local SOCKS port",
            default=local_default,
            validator=_valid_port,
            invalid_message="Enter a port between 1 and 65535.",
        )
        if local is None:
            return None, None
        return int(local), 22

    remote = _run_text_input(
        "Managed SSH",
        "Target port",
        default="22",
        validator=_valid_port,
        invalid_message="Enter a port between 1 and 65535.",
    )
    if remote is None:
        return None, None
    return None, int(remote)


def choose_user(default_user: str = "opc") -> str:
    value = _run_text_input("Managed SSH", "OS user", default=default_user)
    return value or default_user


def confirm_switch_to_pfwd() -> bool:
    return _run_confirm(
        "Managed SSH unavailable",
        "The Bastion plugin is not running. Switch to Port-forwarding instead?",
        default=True,
    )


def confirm_generate_ssh_key() -> bool:
    return _run_confirm(
        "SSH key missing",
        "Generate a new RSA keypair for Bastion sessions?",
        default=False,
    )


def choose_allowlist_mode() -> AllowListMode:
    selected = _run_selection("Allow-list mode for current IP", ["Merge with existing allow-list", "Strict current IP only"])
    return AllowListMode.STRICT if selected == 1 else AllowListMode.MERGE


def choose_resume_or_new(has_sessions: bool) -> str:
    if not has_sessions:
        return "new"
    selected = _run_selection("Session action", ["Resume existing session", "Create new session"])
    return "resume" if selected == 0 else "new"


def choose_session(sessions: List[SessionDescriptor]) -> Optional[SessionDescriptor]:
    if not sessions:
        return None

    def label(s: SessionDescriptor) -> str:
        ip_info = f" {s.target_private_ip}" if s.target_private_ip else ""
        port_info = f":{s.target_port}" if s.target_port else ""
        return f"{s.mode.value}{ip_info}{port_info} {s.session_id[:20]}... ({s.region})"

    selected = _run_selection("Active sessions", [label(s) for s in sessions])
    if selected is None:
        return None
    return sessions[selected]


def run_ssh_command(cmd: str, mode: SessionMode) -> int:
    if mode in (SessionMode.PFWD, SessionMode.SOCKS):
        return _run_background_ssh(cmd)
    return _run_pty_ssh(cmd)


def _run_background_ssh(cmd: str) -> int:
    App, ComposeResult, Horizontal, Vertical, Button, Footer, Header, _Input, RichLog, Static = _load_textual()

    class BackgroundSshApp(App):
        BINDINGS = [("q", "stop", "Stop"), ("ctrl+c", "stop", "Stop")]
        CSS = """
        Screen { padding: 1 2; }
        #body { height: 1fr; }
        #log { height: 1fr; border: solid $accent; }
        Button { margin-right: 1; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.process: Optional[subprocess.Popen[str]] = None
            self.log_widget = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="body"):
                yield Static("SSH tunnel running. Press q or Stop to terminate.")
                yield Static(cmd)
                yield RichLog(id="log", wrap=True)
                with Horizontal():
                    yield Button("Stop", id="stop", variant="error")
            yield Footer()

        def on_mount(self) -> None:
            self.log_widget = self.query_one("#log", RichLog)
            self.process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            threading.Thread(target=self._read_output, daemon=True).start()

        def _read_output(self) -> None:
            assert self.process is not None
            if self.process.stdout is not None:
                for line in self.process.stdout:
                    self.call_from_thread(self.log_widget.write, line.rstrip())
            rc = self.process.wait()
            self.call_from_thread(self.exit, rc)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "stop":
                self.action_stop()

        def action_stop(self) -> None:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()

    return int(BackgroundSshApp().run() or 0)


def _run_pty_ssh(cmd: str) -> int:
    App, ComposeResult, _Horizontal, Vertical, _Button, Footer, Header, _Input, RichLog, Static = _load_textual()

    class PtySshApp(App):
        BINDINGS = [("ctrl+c", "send_interrupt", "Interrupt"), ("ctrl+d", "send_eof", "EOF")]
        CSS = """
        Screen { padding: 1 2; }
        #terminal { height: 1fr; border: solid $accent; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.master_fd: Optional[int] = None
            self.process: Optional[subprocess.Popen[bytes]] = None
            self.terminal_widget = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical():
                yield Static("Managed SSH session. Keyboard input is forwarded to ssh.")
                yield RichLog(id="terminal", wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            self.terminal_widget = self.query_one("#terminal", RichLog)
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd
            argv = shlex.split(cmd)
            self.process = subprocess.Popen(argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
            os.close(slave_fd)
            threading.Thread(target=self._read_pty, daemon=True).start()

        def _read_pty(self) -> None:
            assert self.master_fd is not None
            assert self.process is not None
            while True:
                if self.process.poll() is not None:
                    break
                readable, _, _ = select.select([self.master_fd], [], [], 0.1)
                if not readable:
                    continue
                try:
                    data = os.read(self.master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode(errors="replace")
                self.call_from_thread(self.terminal_widget.write, text.rstrip("\n"))
            rc = self.process.wait()
            self.call_from_thread(self.exit, rc)

        def on_key(self, event) -> None:
            if self.master_fd is None:
                return
            if event.character:
                os.write(self.master_fd, event.character.encode())

        def action_send_interrupt(self) -> None:
            if self.process is not None and self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)

        def action_send_eof(self) -> None:
            if self.master_fd is not None:
                os.write(self.master_fd, b"\x04")

    return int(PtySshApp().run() or 0)
