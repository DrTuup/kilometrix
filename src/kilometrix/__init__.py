"""Kilometrix: track kilometres driven in a Renault Megane E-Tech."""

try:
    from kilometrix._version import __version__
except ImportError:  # running from a source tree that was never built
    __version__ = "0.0.0+unknown"
