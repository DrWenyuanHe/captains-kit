"""Manuscript workflow library for the scientific-manuscript skill.

Standard library only. Reads WordprocessingML with a byte-offset XML tree and
writes changes as byte splices, so untouched bytes (including CRLF inside
EndNote fldData blobs) stay identical.
"""

__version__ = "1.0.0"
