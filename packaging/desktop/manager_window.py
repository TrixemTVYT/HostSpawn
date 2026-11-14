#!/usr/bin/env python3
"""Manager's own app window (Linux): a native GTK window with the WebKit engine showing the dashboard.
No web browser is involved. You sign in on Manager's own sign-in screen (the window keeps the session).
Usage: manager_window.py <manager folder> <port>"""
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2  # noqa: E402

root, port = Path(sys.argv[1]), int(sys.argv[2])
win = Gtk.Window(title="Manager" + (" (DEV)" if port != 8100 else ""))
win.set_default_size(1320, 860)
icon = Path(__file__).with_name("icon.svg")
if icon.exists():
    try:
        win.set_icon_from_file(str(icon))
    except Exception:
        pass
# Keep the sign-in (cookie) and settings between launches, in a folder only this user can read.
data = Path.home() / ".local/share/manager-app" / f"port-{port}"
data.mkdir(parents=True, exist_ok=True)
data.chmod(0o700)
manager = WebKit2.WebsiteDataManager(base_data_directory=str(data), base_cache_directory=str(data / "cache"))
context = WebKit2.WebContext.new_with_website_data_manager(manager)
context.get_cookie_manager().set_persistent_storage(str(data / "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)
view = WebKit2.WebView.new_with_context(context)


def on_decide(_view, decision, kind):
    # Links that leave Manager (e.g. a download page) open in the person's normal browser, not in this window.
    if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION or kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
        uri = decision.get_navigation_action().get_request().get_uri()
        if not uri.startswith(f"http://127.0.0.1:{port}"):
            Gtk.show_uri_on_window(win, uri, Gtk.get_current_event_time())
            decision.ignore()
            return True
    return False


view.connect("decide-policy", on_decide)
view.load_uri(f"http://127.0.0.1:{port}/")
win.add(view)
win.connect("destroy", Gtk.main_quit)
win.show_all()
Gtk.main()
