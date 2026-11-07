"""Speaker coordinate rendering system for the acoustic space canvas."""

import tkinter as tk


class LayoutMap(tk.Canvas):
    """Top-down visual map of a 5.1/5.1.2 speaker array."""

    def __init__(self, parent, width=720, height=500, **kw):
        super().__init__(
            parent, width=width, height=height, bg="#0b0e13",
            highlightthickness=1, highlightbackground="#1f2633", **kw,
        )
        self.map_width = width
        self.map_height = height
        self.nodes = {}
        self._build()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build(self):
        w, h = self.map_width, self.map_height

        # Room outline
        self.create_rectangle(20, 20, w - 20, h - 20, outline="#1f2633", width=2)

        self.create_text(w // 2, 12, text="5.1.2 Top-Down Acoustic Map",
                         fill="#5a6478", font=("Segoe UI", 9))

        positions = {
            "FL": (130, 100),
            "C": (w // 2, 70),
            "FR": (w - 130, 100),
            "Height L": (180, 170),
            "Height R": (w - 180, 170),
            "SL": (90, 300),
            "SR": (w - 90, 300),
            "LFE 1": (150, h - 90),
            "LFE 2": (w - 150, h - 90),
        }
        for label, (x, y) in positions.items():
            self._add_speaker(label, x, y)

        # Listening chair
        cx, cy = w // 2, h - 120
        self.create_rectangle(cx - 46, cy - 28, cx + 46, cy + 28,
                              fill="#1e2430", outline="#5a6478", width=2)
        self.create_text(cx, cy, text="[ Listening Chair ]", fill="#e6edf7",
                         font=("Segoe UI", 9, "bold"))
        self.create_line(cx, cy - 28, cx, cy - 48, fill="#5a6478", width=2, arrow="last")

        # Array labels
        self.create_text(w // 2, 42, text="Front Array", fill="#3a4252",
                         font=("Segoe UI", 8))
        self.create_text(52, 250, text="Surround", fill="#3a4252", font=("Segoe UI", 8))
        self.create_text(w // 2, 145, text="Heights", fill="#3a4252", font=("Segoe UI", 8))

        # ---- Phase correlation meter (beneath listening chair) ----
        self.phase_left = 210
        self.phase_right = 510
        self.phase_center = (self.phase_left + self.phase_right) // 2
        self.phase_half = (self.phase_right - self.phase_left) // 2
        self.phase_top = 428
        self.phase_bottom = 442
        self.create_text(self.phase_center, self.phase_top - 14,
                         text="Phase Correlation", fill="#5a6478", font=("Segoe UI", 8))
        zone_w = (self.phase_right - self.phase_left) // 3
        self.create_rectangle(self.phase_left, self.phase_top,
                              self.phase_left + zone_w, self.phase_bottom,
                              fill="#3a1a1a", outline="")
        self.create_rectangle(self.phase_left + zone_w, self.phase_top,
                              self.phase_left + 2 * zone_w, self.phase_bottom,
                              fill="#3a331a", outline="")
        self.create_rectangle(self.phase_left + 2 * zone_w, self.phase_top,
                              self.phase_right, self.phase_bottom,
                              fill="#1a3a22", outline="")
        self.create_rectangle(self.phase_left, self.phase_top,
                              self.phase_right, self.phase_bottom,
                              outline="#3a4252", width=1, fill="")
        for mark, txt in ((-1, "-1"), (0, "0"), (1, "+1")):
            mx = self.phase_center + mark * self.phase_half
            self.create_text(mx, self.phase_bottom + 10, text=txt, fill="#5a6478",
                             font=("Segoe UI", 8))
        self.phase_needle = self.create_line(
            self.phase_center, self.phase_top - 2, self.phase_center,
            self.phase_bottom + 2, fill="#ffffff", width=3,
        )
        self.phase_value_text = self.create_text(
            self.phase_right + 26, (self.phase_top + self.phase_bottom) // 2,
            text="+0.00", fill="#c9d1e0", font=("Segoe UI", 9, "bold"),
        )

        # ---- Simulated stereo downmix gauges ----
        self.stereo_top = 458
        self.stereo_bottom = 472
        self.stereo_left_rect = (210, 350, self.stereo_top, self.stereo_bottom)
        self.stereo_right_rect = (370, 510, self.stereo_top, self.stereo_bottom)
        for rect, label in (
            (self.stereo_left_rect, "L_mix"),
            (self.stereo_right_rect, "R_mix"),
        ):
            x1, x2, y1, y2 = rect
            self.create_rectangle(x1, y1, x2, y2, fill="#161a22", outline="#3a4252", width=1)
            self.create_text((x1 + x2) // 2, y1 - 10, text=label, fill="#c9d1e0",
                             font=("Segoe UI", 8, "bold"))
        lx1, lx2, ly1, ly2 = self.stereo_left_rect
        rx1, rx2, ry1, ry2 = self.stereo_right_rect
        self.stereo_left_fill = self.create_rectangle(lx1, ly1, lx1, ly2, fill="#2d6a4f", outline="")
        self.stereo_right_fill = self.create_rectangle(rx1, ry1, rx1, ry2, fill="#2d6a4f", outline="")
        self.stereo_left_value = self.create_text(lx2 + 6, (ly1 + ly2) // 2, text="0.00",
                                                  fill="#9aa7bf", font=("Segoe UI", 8), anchor="w")
        self.stereo_right_value = self.create_text(rx2 + 6, (ry1 + ry2) // 2, text="0.00",
                                                   fill="#9aa7bf", font=("Segoe UI", 8), anchor="w")

    def _add_speaker(self, label, x, y):
        base_r = 18
        base = self.create_oval(x - base_r, y - base_r, x + base_r, y + base_r,
                                fill="#161a22", outline="#3a4252", width=2)
        energy = self.create_oval(x - 6, y - 6, x + 6, y + 6,
                                 fill="#2a2f3a", outline="#2a2f3a")
        rings = []
        for i in range(4):
            rr = 24 + i * 7
            ring = self.create_oval(x - rr, y - rr, x + rr, y + rr,
                                    outline="#1a1f28", width=1, fill="")
            rings.append(ring)
        text = self.create_text(x, y + 32, text=label, fill="#c9d1e0",
                                font=("Segoe UI", 9, "bold"))
        self.nodes[label] = {
            "x": x, "y": y, "base": base, "energy": energy,
            "rings": rings, "label": text,
        }

    # ------------------------------------------------------------------ #
    # Visuals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _energy_color(amp):
        amp = max(0.0, min(1.0, amp))
        if amp < 1 / 3:
            t = amp * 3
            r = int(70 + (50 - 70) * t)
            g = int(70 + (220 - 70) * t)
            b = int(80 + (120 - 80) * t)
        elif amp < 2 / 3:
            t = (amp - 1 / 3) * 3
            r = int(50 + (240 - 50) * t)
            g = int(220 + (200 - 220) * t)
            b = int(120 + (60 - 120) * t)
        else:
            t = (amp - 2 / 3) * 3
            r = int(240 + (230 - 240) * t)
            g = int(200 + (60 - 200) * t)
            b = int(60 + (60 - 60) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def update_levels(self, levels):
        """Apply a {label: metrics} mapping to the canvas meters."""
        for label, metrics in (levels or {}).items():
            node = self.nodes.get(label)
            if not node:
                continue
            if isinstance(metrics, dict):
                try:
                    peak = float(metrics.get("peak", 0.0))
                except (TypeError, ValueError):
                    peak = 0.0
                try:
                    rms = float(metrics.get("rms", 0.0))
                except (TypeError, ValueError):
                    rms = 0.0
            else:
                try:
                    peak = rms = float(metrics)
                except (TypeError, ValueError):
                    peak = rms = 0.0
            peak = max(0.0, min(1.0, peak))
            rms = max(0.0, min(1.0, rms))

            energy = max(rms, peak * 0.85)
            energy = max(0.0, min(1.0, energy))

            x, y = node["x"], node["y"]
            r = 6 + energy * 18
            self.coords(node["energy"], x - r, y - r, x + r, y + r)
            color = self._energy_color(energy)
            self.itemconfig(node["energy"], fill=color, outline=color)
            for i, ring in enumerate(node["rings"]):
                rr = 24 + i * 7 + peak * 4
                self.coords(ring, x - rr, y - rr, x + rr, y + rr)
                if peak > 0.05:
                    self.itemconfig(ring, outline=color, width=1 + int(peak * 2))
                else:
                    self.itemconfig(ring, outline="#1a1f28", width=1)

    @staticmethod
    def _extract_phase(payload):
        for key in ("phase", "phase_correlation", "correlation", "_phase", "phaseCorrelation"):
            if key in payload:
                value = payload[key]
                if isinstance(value, dict):
                    for sub in ("value", "phase", "correlation", "rms", "peak"):
                        if sub in value:
                            value = value[sub]
                            break
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _extract_stereo(payload):
        left = None
        right = None
        left_keys = ("L_mix", "Stereo L", "stereo_left", "L", "left")
        right_keys = ("R_mix", "Stereo R", "stereo_right", "R", "right")
        for key in left_keys:
            if key in payload:
                left = payload[key]
                break
        for key in right_keys:
            if key in payload:
                right = payload[key]
                break
        return left, right

    def update_phase(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        value = max(-1.0, min(1.0, value))
        x = self.phase_center + int(value * self.phase_half)
        self.coords(self.phase_needle, x, self.phase_top - 2, x, self.phase_bottom + 2)
        self.itemconfig(self.phase_value_text, text=f"{value:+.2f}")

    def _metric_amp(self, metrics):
        if isinstance(metrics, dict):
            for key in ("rms", "peak", "value", "amplitude"):
                if key in metrics:
                    try:
                        return float(metrics[key])
                    except (TypeError, ValueError):
                        pass
            return 0.0
        try:
            return float(metrics)
        except (TypeError, ValueError):
            return 0.0

    def update_stereo(self, left, right):
        pairs = (
            (left, self.stereo_left_rect, self.stereo_left_fill, self.stereo_left_value),
            (right, self.stereo_right_rect, self.stereo_right_fill, self.stereo_right_value),
        )
        for metrics, rect, fill_id, value_id in pairs:
            if metrics is None:
                continue
            x1, x2, y1, y2 = rect
            amp = self._metric_amp(metrics)
            amp = max(0.0, min(1.0, amp))
            width = int((x2 - x1) * amp)
            self.coords(fill_id, x1, y1, x1 + width, y2)
            color = self._energy_color(amp)
            self.itemconfig(fill_id, fill=color)
            self.itemconfig(value_id, text=f"{amp:.2f}")

    def update_from_payload(self, payload):
        self.update_levels(payload)
        phase = self._extract_phase(payload)
        if phase is not None:
            self.update_phase(phase)
        left, right = self._extract_stereo(payload)
        if left is not None or right is not None:
            self.update_stereo(left, right)