"""Licences and feature locks (compiled to a binary module when packaged).

Everything that decides whether a locked feature is available lives in this
package; the rest of the app only calls :mod:`app._guard.gate`.
"""
