"""suite_link: the small, Qt-free module the lab-suite programs share to talk to each other.

Copy this folder unchanged into each separately packaged program (Browser, Measurement,
Lab LaTeX, Time Domain). It only builds and reads links and registers the link scheme with the
operating system; it never opens a network connection and never runs a received command.
"""

from .links import ACTIONS, APPS, Link, LinkError, build_link, parse_link, scheme_of
from .registration import (LinuxRegistrar, MacRegistrar, Registrar, WindowsRegistrar, current_registrar,
                           launch_command, plist_entry)

__all__ = ["ACTIONS", "APPS", "Link", "LinkError", "build_link", "parse_link", "scheme_of",
           "Registrar", "WindowsRegistrar", "MacRegistrar", "LinuxRegistrar", "current_registrar",
           "launch_command", "plist_entry"]
