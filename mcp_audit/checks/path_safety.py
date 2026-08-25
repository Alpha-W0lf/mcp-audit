"""Path-safety checks for filesystem-adjacent tools.

Seeded by modelcontextprotocol/servers#4686: on POSIX, a Windows-style path
(`C:\\Users\\me\\file.md`) passed validation and was created as a literal
backslash filename inside the sandbox.

Planned probes (v0.2):
- Discover path-accepting tools (write_file / read_file / create_directory
  name heuristics + server profile).
- On POSIX hosts, assert drive-letter forms (`C:\\...`, `Z:/`) are rejected
  with an error result — not silently written as literal filenames.
- Assert UNC forms (`\\\\server\\share`) are handled per the server's
  documented policy (rejection or explicit normalization — never silent).

Requires a sandboxed server instance (writes are expected during the probe).
"""

from __future__ import annotations


def probe_path_safety() -> None:
    raise NotImplementedError("v0.2 — see module docstring for the spec.")
