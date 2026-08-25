"""Path-safety checks for filesystem-adjacent tools — NOT YET IMPLEMENTED.

Seeded by modelcontextprotocol/servers#4686: on POSIX, a Windows-style path
(`C:\\Users\\me\\file.md`) passed validation and was created as a literal
backslash filename inside the sandbox. Planned ID: PATHSAFE001.

Planned probes:
- Discover path-accepting tools (write_file / read_file / create_directory
  name heuristics + server profile).
- On POSIX hosts, assert drive-letter forms (`C:\\...`, `Z:/`) are rejected
  with an error result — not silently written as literal filenames.
- Assert UNC forms (`\\\\server\\share`) are handled per the server's
  documented policy (rejection or explicit normalization — never silent).

Requires a sandboxed server instance (writes are expected during the probe);
will be gated behind the same annotation safety model as RUNTIME001.
"""

from __future__ import annotations

CITATION_4686 = "https://github.com/modelcontextprotocol/servers/issues/4686"
