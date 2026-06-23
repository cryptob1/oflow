#!/usr/bin/env python3
"""oflow on-screen recording overlay.

A small, borderless, click-through layer-shell window anchored bottom-center
that shows a recording dot and a live audio level meter while oflow records.

It is spawned by the oflow backend on record start. The backend streams audio
levels (one float per packet, plus a final "stop" sentinel) over a Unix
datagram socket at $XDG_RUNTIME_DIR/oflow/osd.sock. The overlay animates a
scrolling waveform from those levels and exits when recording stops (or after
an idle timeout, as a safety net).

Requires: gtk4, gtk4-layer-shell, python-gobject, python-cairo.
"""
import math
import os
import socket
import struct

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LayerShell  # noqa: E402
import cairo  # noqa: E402

RUNTIME_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "oflow")
SOCK_PATH = os.path.join(RUNTIME_DIR, "osd.sock")

PILL_H = 54           # the meter capsule
WIDTH, HEIGHT = 340, PILL_H
PAD = 14
N_BARS = 42
IDLE_TIMEOUT_US = 2_000_000  # exit if no packet for 2s (backend died / missed stop)
FPS_MS = 16  # ~60fps

class Osd(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.oflow.osd")
        self.levels = [0.0] * N_BARS   # rolling history, newest at the end
        self.target = 0.0              # latest level from the backend
        self.smooth = 0.0              # smoothed current level
        self.last_packet_us = GLib.get_monotonic_time()
        self.stopping = False
        self.fade = 1.0                # 1.0 visible -> 0.0 gone (on stop)
        self.pulse = 0.0               # recording-dot pulse phase
        self.sock = None

    # ------------------------------------------------------------------ setup
    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self)
        self.win = win

        LayerShell.init_for_window(win)
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, 90)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        LayerShell.set_namespace(win, "oflow-osd")

        win.set_decorated(False)
        win.set_default_size(WIDTH, HEIGHT)

        # Transparent window background so the rounded pill floats.
        css = Gtk.CssProvider()
        css.load_from_string("window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        area = Gtk.DrawingArea()
        area.set_content_width(WIDTH)
        area.set_content_height(HEIGHT)
        area.set_draw_func(self._draw)
        self.area = area
        win.set_child(area)
        win.present()

        self._make_click_through()
        self._open_socket()
        GLib.timeout_add(FPS_MS, self._tick)

    def _make_click_through(self):
        """Empty input region => pointer events pass through to windows below."""
        surface = self.win.get_surface()
        if surface is not None:
            try:
                surface.set_input_region(cairo.Region())
            except Exception:
                pass

    def _open_socket(self):
        try:
            os.makedirs(RUNTIME_DIR, exist_ok=True)
            if os.path.exists(SOCK_PATH):
                os.remove(SOCK_PATH)
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
            self.sock.bind(SOCK_PATH)
            GLib.io_add_watch(self.sock.fileno(), GLib.IO_IN, self._on_socket)
        except OSError:
            self.sock = None

    # --------------------------------------------------------------- data in
    def _on_socket(self, *_):
        try:
            while True:
                data = self.sock.recv(64)
                if not data:
                    break
                self.last_packet_us = GLib.get_monotonic_time()
                if data[:4] == b"stop":
                    self.stopping = True
                else:
                    try:
                        self.target = max(0.0, min(1.0, struct.unpack("<f", data[:4])[0]))
                    except struct.error:
                        pass
        except (BlockingIOError, OSError):
            pass
        return True

    # ------------------------------------------------------------- animation
    def _tick(self):
        # Idle safety net: if the backend stopped sending, fade out.
        if GLib.get_monotonic_time() - self.last_packet_us > IDLE_TIMEOUT_US:
            self.stopping = True

        # Smooth the incoming level and push it onto the rolling history.
        self.smooth += (self.target - self.smooth) * 0.35
        self.levels.append(self.smooth)
        if len(self.levels) > N_BARS:
            self.levels.pop(0)
        # decay the latest target so the meter falls when you go quiet
        self.target *= 0.82
        self.pulse = (self.pulse + 0.07) % (2 * math.pi)

        if self.stopping:
            self.fade -= 0.08
            if self.fade <= 0:
                if self.sock:
                    try:
                        self.sock.close()
                        os.remove(SOCK_PATH)
                    except OSError:
                        pass
                self.quit()
                return False

        self.area.queue_draw()
        return True

    # ------------------------------------------------------------------ draw
    def _draw(self, _area, cr, w, h, *_):
        a = self.fade
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        ph = PILL_H  # the meter capsule fills the whole overlay

        # Rounded pill background
        self._round_rect(cr, 0.5, 0.5, w - 1, h - 1, ph / 2)
        cr.set_source_rgba(0.09, 0.10, 0.15, 0.92 * a)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06 * a)
        cr.set_line_width(1)
        cr.stroke()

        # Pulsing recording dot
        dot_x, dot_y = PAD + 6, ph / 2
        glow = 0.5 + 0.5 * math.sin(self.pulse)
        cr.arc(dot_x, dot_y, 6 + 1.5 * glow, 0, 2 * math.pi)
        cr.set_source_rgba(0.95, 0.27, 0.36, (0.85 + 0.15 * glow) * a)
        cr.fill()

        # Level meter / waveform bars
        x0 = PAD + 22
        x1 = w - PAD
        span = x1 - x0
        bar_gap = span / N_BARS
        bar_w = max(2.0, bar_gap * 0.55)
        mid = ph / 2
        max_h = ph * 0.36
        n = len(self.levels)
        for i, lvl in enumerate(self.levels):
            # perceptual curve + tiny floor so idle still shows a flat line
            mag = (lvl ** 0.65)
            bh = max(2.0, mag * 2 * max_h)
            x = x0 + i * bar_gap
            # newer bars (right) brighter
            t = i / max(1, n - 1)
            cr.set_source_rgba(0.40 + 0.45 * t, 0.74, 0.99, (0.35 + 0.6 * t) * a)
            self._round_rect(cr, x, mid - bh / 2, bar_w, bh, bar_w / 2)
            cr.fill()

    @staticmethod
    def _round_rect(cr, x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.arc(x + w - r, y + r, r, 3 * math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.close_path()


if __name__ == "__main__":
    Osd().run(None)
