"""Multi-byte encoding checks at chunk boundaries — NOT YET IMPLEMENTED.

Seeded by modelcontextprotocol/servers#4666: headFile/tailFile decoded fixed
1024-byte chunks independently, corrupting multi-byte UTF-8 sequences that
straddled a boundary. Planned ID: ENCODING001.

Planned probes:
- Discover file-reading tools (name/description heuristics + server profile).
- Build fixture files with multi-byte characters placed exactly at the
  server's reported/observed chunk size.
- Assert returned content is valid UTF-8 and matches the expected slice.

Requires a side-effect-safe profile (read-only tools only).
"""

from __future__ import annotations

CITATION_4666 = "https://github.com/modelcontextprotocol/servers/issues/4666"
