"""Main application window: header controls, canvas orchestration, footer."""

import os
import queue
import tkinter as tk
from tkinter import filedialog

from matrixtrack import __version__

from matrixtrack.gui.layout_map import LayoutMap
from matrixtrack.gui.monitor import QueueMonitor, AudioWorker


class MatrixTrackApp:
    def __init__(self, root):
        self.root = root
        root.title(f"MatrixTrack - v{__version__} | Multi-Channel Audio Auditor")
        root.geometry("760x620")
        root.minsize(700, 580)
        root.configure(bg="#0b0e13")

        # ---- Header control bar ----
        header = tk.Frame(root, bg="#11151c", padx=10, pady=8)
        header.pack(fill="x")

        file_row = tk.Frame(header, bg="#11151c")
        file_row.pack(fill="x")

        self.open_btn = tk.Button(
            file_row, text="Open Multi-Channel File", command=self.open_file,
            bg="#1f2633", fg="#e6edf7", relief="flat", padx=10, pady=4,
            activebackground="#2a3344", activeforeground="#ffffff",
        )
        self.open_btn.pack(side="left")

        self.file_path_var = tk.StringVar(value="No file selected")
        path_label = tk.Label(
            file_row, textvariable=self.file_path_var, bg="#11151c",
            fg="#9aa7bf", anchor="w", padx=10,
        )
        path_label.pack(side="left", fill="x", expand=True)

        ctrl_row = tk.Frame(header, bg="#11151c", pady=6)
        ctrl_row.pack(fill="x")

        self.play_btn = tk.Button(
            ctrl_row, text="Play/Monitor", command=self.start_monitor,
            bg="#2d6a4f", fg="#ffffff", relief="flat", padx=12, pady=4,
            activebackground="#3a8a66", activeforeground="#ffffff",
        )
        self.play_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(
            ctrl_row, text="Stop", command=self.stop_monitor,
            bg="#6a2d2d", fg="#ffffff", relief="flat", padx=12, pady=4,
            state="disabled", activebackground="#8a3a3a", activeforeground="#ffffff",
        )
        self.stop_btn.pack(side="left")

        self.monitor_state_var = tk.StringVar(value="Idle")
        tk.Label(ctrl_row, textvariable=self.monitor_state_var, bg="#11151c",
                 fg="#9aa7bf", padx=10).pack(side="left")

        self.monitor_mode_var = tk.StringVar(value="Surround")
        self.mode_btn = tk.Button(
            ctrl_row, text="Monitor: Surround", command=self.toggle_monitor_mode,
            bg="#1f2633", fg="#e6edf7", relief="flat", padx=10, pady=4,
            activebackground="#2a3344", activeforeground="#ffffff",
        )
        self.mode_btn.pack(side="left", padx=(6, 0))

        # ---- Canvas ----
        canvas_frame = tk.Frame(root, bg="#0b0e13")
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.canvas = LayoutMap(canvas_frame, width=720, height=500)
        self.canvas.pack(expand=True)

        # ---- Footer status bar ----
        footer = tk.Frame(root, bg="#11151c", padx=10, pady=6)
        footer.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Status: Ready. Open a file or press Play/Monitor.")
        status_label = tk.Label(footer, textvariable=self.status_var, bg="#11151c",
                                fg="#c9d1e0", anchor="w")
        status_label.pack(fill="x")

        # ---- Queue + monitor + worker ----
        self.queue = queue.Queue(maxsize=4)
        self.poller = QueueMonitor(self.canvas, self.queue, 30)
        self.worker = None

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open Multi-Channel File",
            filetypes=[
                ("Supported Multichannel Audio", "*.wav *.flac"),
                ("WAV Audio", "*.wav"),
                ("FLAC Audio", "*.flac"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.file_path_var.set(path)
            self.status_var.set(f"Status: Loaded {os.path.basename(path)}")
            self.monitor_state_var.set("File loaded")

    def start_monitor(self):
        if self.worker is not None and self.worker.running:
            return
        path = self.file_path_var.get()
        if path == "No file selected":
            path = None
        self.worker = AudioWorker(
            self.queue, path=path, block_size=1024, mode=self.monitor_mode_var.get(),
        )
        self.worker.start()
        self.poller.start()
        self.play_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.monitor_state_var.set("Monitoring")
        if path:
            self.status_var.set("Status: Monitoring Active...")
        else:
            self.status_var.set("Status: Monitoring Active (no file selected)...")

    def stop_monitor(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.poller.stop()
        self.play_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.monitor_state_var.set("Idle")
        self.status_var.set("Status: Stopped")

    def toggle_monitor_mode(self):
        if self.monitor_mode_var.get() == "Surround":
            self.monitor_mode_var.set("Stereo Downmix")
        else:
            self.monitor_mode_var.set("Surround")
        self.mode_btn.config(text=f"Monitor: {self.monitor_mode_var.get()}")

        if self.worker is not None and self.worker.running:
            path = self.file_path_var.get()
            if path == "No file selected":
                path = None
            self.worker.stop()
            self.worker = AudioWorker(
                self.queue, path=path, block_size=1024, mode=self.monitor_mode_var.get(),
            )
            self.worker.start()

    def on_close(self):
        try:
            self.stop_monitor()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    MatrixTrackApp(root)
    root.mainloop()
