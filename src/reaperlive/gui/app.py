"""The standalone reaperlive window.

Nothing here runs inside a DAW. You launch this, build a project, and then it
hands the finished project to REAPER or Live.

Every option maps onto a command line flag and is parsed by the same argparse
parser the CLI uses, so the two front ends cannot drift apart.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from reaperlive import __version__
from reaperlive.gui.launch import open_project, reveal
from reaperlive.gui.runner import BuildJob, DoneMessage, LogMessage, StageMessage

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # pragma: no cover - depends on the Python build
    raise SystemExit(
        "The reaperlive window needs Tkinter, which this Python was built "
        "without.\n"
        "  Debian/Ubuntu:  sudo apt install python3-tk\n"
        "  Fedora:         sudo dnf install python3-tkinter\n"
        "  macOS/Windows:  use the python.org installer, which bundles it\n"
        f"({exc})"
    ) from exc

AUDIO_TYPES = [
    ("Audio", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.aiff *.aif *.opus *.wma"),
    ("All files", "*.*"),
]

SEPARATORS = [("Demucs (Meta)", "demucs"),
              ("RoFormer / MDX", "roformer"),
              ("None - just the mix", "none")]
MODELS = ["htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra", "mdx_extra_q"]
TEMPO_MODES = [("One tempo for the song", "constant"),
               ("Follow the song, per bar", "measure"),
               ("Follow the song, per beat", "beat")]
TARGETS = [("REAPER", "reaper"), ("Ableton Live", "ableton"), ("Both", "both")]


def settings_path() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "reaperlive" / "settings.json"


class App:
    def __init__(self, root: "tk.Misc"):
        self.root = root
        self.job: Optional[BuildJob] = None
        self.result = None

        root.title(f"reaperlive {__version__}")
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:  # pragma: no cover - theme availability varies
            pass

        self.source = tk.StringVar()
        self.outdir = tk.StringVar(value=str(Path.home() / "reaperlive projects"))
        self.name = tk.StringVar()
        self.target = tk.StringVar(value="reaper")
        self.separator = tk.StringVar(value="demucs")
        self.model = tk.StringVar(value="htdemucs")
        self.vocals_only = tk.BooleanVar(value=False)
        self.tempo_mode = tk.StringVar(value="measure")
        self.bpm = tk.StringVar()
        self.time_sig = tk.StringVar(value="4/4")
        self.lead_in = tk.StringVar(value="1")
        self.mark_sections = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Ready.")

        self._load_settings()
        self._build_widgets()
        self._poll()

    # ---------------------------------------------------------------- layout
    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        song = ttk.LabelFrame(outer, text="Song", padding=10)
        song.grid(row=0, column=0, sticky="ew")
        song.columnconfigure(1, weight=1)

        ttk.Label(song, text="Source").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(song, textvariable=self.source).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(song, text="File...", command=self._pick_file).grid(row=0, column=2)
        ttk.Label(song, text="A YouTube link, or an audio file on this machine",
                  foreground="#666").grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(song, text="Save to").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(song, textvariable=self.outdir).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(song, text="Folder...", command=self._pick_folder).grid(row=2, column=2)

        ttk.Label(song, text="Name").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(song, textvariable=self.name).grid(row=3, column=1, sticky="ew", padx=6)
        ttk.Label(song, text="Optional - taken from the track title otherwise",
                  foreground="#666").grid(row=4, column=1, sticky="w", padx=6)

        opts = ttk.LabelFrame(outer, text="Project", padding=10)
        opts.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        opts.columnconfigure(1, weight=1)
        opts.columnconfigure(3, weight=1)

        ttk.Label(opts, text="Open in").grid(row=0, column=0, sticky="w", pady=3)
        daws = ttk.Frame(opts)
        daws.grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        for label, value in TARGETS:
            ttk.Radiobutton(daws, text=label, value=value,
                            variable=self.target).pack(side="left", padx=(0, 12))

        ttk.Label(opts, text="Stems").grid(row=1, column=0, sticky="w", pady=3)
        self.separator_box = ttk.Combobox(
            opts, state="readonly", values=[label for label, _ in SEPARATORS])
        self.separator_box.grid(row=1, column=1, sticky="ew", padx=6)
        self.separator_box.bind("<<ComboboxSelected>>", self._on_separator)
        self._select_combo(self.separator_box, SEPARATORS, self.separator.get())

        ttk.Label(opts, text="Model").grid(row=1, column=2, sticky="e")
        self.model_box = ttk.Combobox(opts, state="readonly", values=MODELS,
                                      textvariable=self.model)
        self.model_box.grid(row=1, column=3, sticky="ew", padx=6)

        ttk.Checkbutton(opts, text="Vocals and backing only (much faster)",
                        variable=self.vocals_only).grid(
            row=2, column=1, columnspan=3, sticky="w", padx=6)

        ttk.Label(opts, text="Tempo").grid(row=3, column=0, sticky="w", pady=3)
        self.tempo_box = ttk.Combobox(
            opts, state="readonly", values=[label for label, _ in TEMPO_MODES])
        self.tempo_box.grid(row=3, column=1, sticky="ew", padx=6)
        self.tempo_box.bind("<<ComboboxSelected>>", self._on_tempo_mode)
        self._select_combo(self.tempo_box, TEMPO_MODES, self.tempo_mode.get())

        ttk.Label(opts, text="Known BPM").grid(row=3, column=2, sticky="e")
        ttk.Entry(opts, textvariable=self.bpm, width=10).grid(row=3, column=3,
                                                              sticky="w", padx=6)

        ttk.Label(opts, text="Time sig").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(opts, textvariable=self.time_sig, width=10).grid(
            row=4, column=1, sticky="w", padx=6)
        ttk.Label(opts, text="Count-in bars").grid(row=4, column=2, sticky="e")
        ttk.Spinbox(opts, from_=0, to=8, width=8, textvariable=self.lead_in).grid(
            row=4, column=3, sticky="w", padx=6)

        ttk.Checkbutton(opts, text="Mark where the vocals are",
                        variable=self.mark_sections).grid(
            row=5, column=1, columnspan=3, sticky="w", padx=6)

        actions = ttk.Frame(outer)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 6))
        self.build_button = ttk.Button(actions, text="Build project",
                                       command=self.start_build)
        self.build_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancel", state="disabled",
                                        command=self.cancel_build)
        self.cancel_button.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(outer, maximum=1000)
        self.progress.grid(row=3, column=0, sticky="ew")
        ttk.Label(outer, textvariable=self.status, foreground="#444").grid(
            row=4, column=0, sticky="w", pady=(4, 6))

        outer.rowconfigure(5, weight=1)
        log_frame = ttk.Frame(outer)
        log_frame.grid(row=5, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled",
                           background="#1d1f21", foreground="#d8d8d8",
                           insertbackground="#d8d8d8", relief="flat")
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(log_frame, command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)
        self.log.tag_configure("error", foreground="#ff8a80")
        self.log.tag_configure("ok", foreground="#9be89b")

        finish = ttk.Frame(outer)
        finish.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        self.open_reaper = ttk.Button(finish, text="Open in REAPER", state="disabled",
                                      command=lambda: self._open("reaper"))
        self.open_reaper.pack(side="left")
        self.open_ableton = ttk.Button(finish, text="Open in Ableton", state="disabled",
                                       command=lambda: self._open("ableton"))
        self.open_ableton.pack(side="left", padx=6)
        self.show_folder = ttk.Button(finish, text="Show folder", state="disabled",
                                      command=self._reveal)
        self.show_folder.pack(side="left")

        self._on_separator()

    @staticmethod
    def _select_combo(box, pairs, value) -> None:
        for label, item in pairs:
            if item == value:
                box.set(label)
                return
        box.set(pairs[0][0])

    @staticmethod
    def _combo_value(box, pairs) -> str:
        for label, value in pairs:
            if label == box.get():
                return value
        return pairs[0][1]

    # --------------------------------------------------------------- actions
    def _pick_file(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose an audio file",
                                            filetypes=AUDIO_TYPES)
        if chosen:
            self.source.set(chosen)

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Where should projects go?")
        if chosen:
            self.outdir.set(chosen)

    def _on_separator(self, _event=None) -> None:
        self.separator.set(self._combo_value(self.separator_box, SEPARATORS))
        separating = self.separator.get() != "none"
        self.model_box.configure(state="readonly" if separating else "disabled")

    def _on_tempo_mode(self, _event=None) -> None:
        self.tempo_mode.set(self._combo_value(self.tempo_box, TEMPO_MODES))

    def argv(self) -> list[str]:
        """The equivalent command line, which is also how options are parsed."""
        self._on_separator()
        self._on_tempo_mode()
        argv = [self.source.get().strip(),
                "-o", self.outdir.get().strip(),
                "-t", self.target.get(),
                "--separator", self.separator.get(),
                "--tempo-mode", self.tempo_mode.get(),
                "--time-sig", self.time_sig.get().strip() or "4/4",
                "--lead-in-bars", str(self.lead_in.get() or "1")]
        if self.name.get().strip():
            argv += ["-n", self.name.get().strip()]
        if self.separator.get() != "none":
            argv += ["--model", self.model.get()]
            if self.vocals_only.get():
                argv += ["--two-stems", "vocals"]
        if self.bpm.get().strip():
            argv += ["--bpm", self.bpm.get().strip()]
        if not self.mark_sections.get():
            argv.append("--no-markers")
        return argv

    def start_build(self) -> None:
        from reaperlive.cli import build_parser, options_from_args

        if not self.source.get().strip():
            messagebox.showwarning("Nothing to build",
                                   "Paste a YouTube link or choose an audio file.")
            return
        if self.job and self.job.running:
            return
        try:
            options = options_from_args(build_parser().parse_args(self.argv()))
        except SystemExit as exc:
            messagebox.showerror("Check the settings", str(exc) or "Invalid settings.")
            return

        self.result = None
        self._set_running(True)
        self._clear_log()
        self._write(f"Building from {options.source}", "ok")
        if options.separation.backend != "none":
            self._write("Separation takes a few minutes on CPU. You can cancel.")
        self._save_settings()
        self.job = BuildJob(options)
        self.job.start()

    def cancel_build(self) -> None:
        if self.job and self.job.running:
            self.job.cancel()
            self.status.set("Cancelling...")
            self.cancel_button.configure(state="disabled")

    def _open(self, daw: str) -> None:
        if not self.result:
            return
        project = (self.result.reaper_project if daw == "reaper"
                   else self.result.ableton_project)
        if not project:
            return
        try:
            open_project(project, daw)
        except Exception as exc:  # noqa: BLE001 - report, never crash the window
            messagebox.showerror("Could not open the project", str(exc))

    def _reveal(self) -> None:
        if self.result:
            reveal(self.result.project_dir)

    # ------------------------------------------------------------ event loop
    def _poll(self) -> None:
        if self.job:
            for message in self.job.drain():
                self._handle(message)
        self.root.after(120, self._poll)

    def _handle(self, message) -> None:
        if isinstance(message, LogMessage):
            self._write(message.text,
                        "error" if message.level >= logging.WARNING else None)
        elif isinstance(message, StageMessage):
            stage = message.stage
            self.progress.configure(value=stage.overall * 1000)
            self.status.set(f"Step {stage.index} of {stage.total} - "
                            f"{stage.label}{stage.detail}")
        elif isinstance(message, DoneMessage):
            self._finish(message)

    def _finish(self, message: DoneMessage) -> None:
        self._set_running(False)
        if message.cancelled:
            self.progress.configure(value=0)
            self.status.set("Cancelled.")
            self._write("Cancelled.", "error")
            return
        if message.error:
            self.progress.configure(value=0)
            self.status.set("Build failed.")
            self._write(f"error: {message.error}", "error")
            messagebox.showerror("Build failed", message.error)
            return

        result = message.result
        self.result = result
        self.progress.configure(value=1000)
        grid = result.grid
        self.status.set(
            f"Done - {grid.initial_bpm:.1f} BPM, {len(result.stems)} stems, "
            f"{len(result.sections)} sections")
        self._write(f"Finished: {result.project_dir}", "ok")
        self.show_folder.configure(state="normal")
        self.open_reaper.configure(
            state="normal" if result.reaper_project else "disabled")
        self.open_ableton.configure(
            state="normal" if result.ableton_project else "disabled")

    def _set_running(self, running: bool) -> None:
        self.build_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.configure(value=0)
            for button in (self.open_reaper, self.open_ableton, self.show_folder):
                button.configure(state="disabled")

    def _write(self, text: str, tag: Optional[str] = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -------------------------------------------------------------- settings
    def _load_settings(self) -> None:
        try:
            data = json.loads(settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, var in self._persisted().items():
            if key in data:
                try:
                    var.set(data[key])
                except tk.TclError:  # pragma: no cover - corrupt settings
                    pass

    def _save_settings(self) -> None:
        path = settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {key: var.get() for key, var in self._persisted().items()}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass  # settings are a convenience, never a reason to fail a build

    def _persisted(self) -> dict:
        return {
            "outdir": self.outdir, "target": self.target,
            "separator": self.separator, "model": self.model,
            "vocals_only": self.vocals_only, "tempo_mode": self.tempo_mode,
            "time_sig": self.time_sig, "lead_in": self.lead_in,
            "mark_sections": self.mark_sections,
        }


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = tk.Tk()
    root.geometry("780x760")
    root.minsize(640, 620)
    app = App(root)
    if argv:
        app.source.set(argv[0])
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
