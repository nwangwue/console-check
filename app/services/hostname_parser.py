import re
from dataclasses import dataclass


@dataclass
class ParseResult:
    hostname: str | None = None
    confidence: str = "none"       # high, medium, low, none
    match_type: str = "none"       # show_run, prompt, config_mode, post_auth_fail, none
    raw_matched_line: str = ""


# Generic/default Cisco hostnames that indicate unconfigured devices
DEFAULT_HOSTNAMES = {"router", "switch", "ap"}

# ANSI escape code pattern
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


class HostnameParser:
    # Patterns ordered from most specific to least specific.
    PATTERNS = [
        # Pattern 1: "hostname XXXXX" from show run output
        ("show_run", re.compile(r'hostname\s+(\S+)', re.IGNORECASE)),
        # Pattern 2: Config mode prompt (must check before plain prompt)
        ("config_mode", re.compile(r'^([A-Za-z][A-Za-z0-9._-]+)\(config[^)]*\)#\s*$', re.MULTILINE)),
        # Pattern 3: Standard IOS-XE prompt with # (enable mode)
        ("prompt", re.compile(r'^([A-Za-z][A-Za-z0-9._-]+)#\s*$', re.MULTILINE)),
        # Pattern 4: Standard IOS-XE prompt with > (user mode)
        ("prompt", re.compile(r'^([A-Za-z][A-Za-z0-9._-]+)>\s*$', re.MULTILINE)),
    ]

    def parse(self, raw_output: str) -> ParseResult:
        """Extract hostname from raw console output."""
        if not raw_output or not raw_output.strip():
            return ParseResult()

        cleaned = self._clean_output(raw_output)
        if not cleaned.strip():
            return ParseResult()

        candidates: list[tuple[str, str, str]] = []  # (hostname, match_type, matched_line)

        # Try each pattern
        for match_type, pattern in self.PATTERNS:
            for match in pattern.finditer(cleaned):
                hostname = match.group(1).strip()
                if hostname:
                    candidates.append((hostname, match_type, match.group(0).strip()))

        if not candidates:
            # Try post-auth-fail: look for hostname on any line with # or >
            post_auth = re.compile(r'(?:^|\n)\s*([A-Za-z][A-Za-z0-9._-]{2,})[#>]\s*$', re.MULTILINE)
            for match in post_auth.finditer(cleaned):
                hostname = match.group(1).strip()
                candidates.append((hostname, "post_auth_fail", match.group(0).strip()))

        if not candidates:
            return ParseResult()

        # Filter out generic default hostnames if we have better candidates
        non_default = [c for c in candidates if c[0].lower() not in DEFAULT_HOSTNAMES]
        if non_default:
            candidates = non_default

        # Prefer show_run matches
        show_run = [c for c in candidates if c[1] == "show_run"]
        if show_run:
            hostname = show_run[0][0]
            return ParseResult(
                hostname=hostname,
                confidence="high",
                match_type="show_run",
                raw_matched_line=show_run[0][2],
            )

        # Use the last candidate (most recent prompt in output)
        best = candidates[-1]
        hostname = best[0]

        # Determine confidence
        if hostname.lower() in DEFAULT_HOSTNAMES:
            confidence = "low"
        elif len(candidates) > 1:
            confidence = "high"
        else:
            confidence = "medium"

        return ParseResult(
            hostname=hostname,
            confidence=confidence,
            match_type=best[1],
            raw_matched_line=best[2],
        )

    def _clean_output(self, raw: str) -> str:
        """Clean raw console paste."""
        # Strip ANSI escape codes
        result = ANSI_ESCAPE.sub('', raw)
        # Normalize line endings
        result = result.replace('\r\n', '\n').replace('\r', '\n')
        # Strip leading/trailing whitespace per line, remove blank lines at edges
        lines = result.split('\n')
        lines = [line.rstrip() for line in lines]
        # Remove blank lines at start/end only
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return '\n'.join(lines)
