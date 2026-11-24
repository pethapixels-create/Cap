#!/usr/bin/env python3
# control.py: a GTK window with one button, as a control that the AT-SPI bus works on this box.
# Lists every app AT-SPI knows after 4 s, then exits.
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib, Gtk  # noqa: E402

win = Gtk.Window(title="CD control")
win.add(Gtk.Button(label="Control button"))
win.show_all()


def report():
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is not None:
            print(f"atspi app: {app.get_name()!r} pid={app.get_process_id()} children={app.get_child_count()}")
    sys.stdout.flush()
    Gtk.main_quit()


GLib.timeout_add(4000, report)
Gtk.main()
