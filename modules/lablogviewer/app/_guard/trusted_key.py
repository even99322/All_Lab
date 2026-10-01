"""Public keys that sign LabLogViewer licences.

Written by LabLogViewer_Plugins/DevTools. Only PUBLIC halves live here; they can check
licences but never make one. A licence signed by any key listed here is accepted.

PUBLIC_KEY_HEX     the first licence issuer.
ADDITIONAL_KEYS    further issuers (for example a successor with a key of their own),
                   name -> key. Adding one keeps every earlier licence valid; removing a
                   key in a later version stops accepting licences it signed.
Empty = no licence is accepted (the normal unlock by experience still works).
"""

PUBLIC_KEY_HEX = "c6cee1de9525f60c84f2d8911f340633ac4ade7f7f58cb1f8e2ea30db948ff78"
ADDITIONAL_KEYS: dict[str, str] = {}
