"""Pure, bounded normalization of untrusted evidence text."""

import argparse
import hashlib
import json
import re
import sys
import unicodedata

SECRET_PATTERNS = (
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.~=+/]{12,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?s)-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----"),
)
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC_PATTERN = re.compile(r"(?s)\x1b\][^\x07]*(?:\x07|\x1b\\|$)")
BIDI_CONTROLS = frozenset({"\u061c", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"})


def _decode(raw):
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), True


def _redact(text):
    redacted = False
    for pattern in SECRET_PATTERNS:
        text, count = pattern.subn("[REDACTED]", text)
        redacted = redacted or count > 0
    return text, redacted


def _neutralize_controls(text):
    changed = False

    def replace_terminal(_match):
        nonlocal changed
        changed = True
        return "[CONTROL_SEQUENCE]"

    text = ANSI_PATTERN.sub(replace_terminal, text)
    text = OSC_PATTERN.sub(replace_terminal, text)
    result = []
    for char in text:
        code = ord(char)
        if char in BIDI_CONTROLS:
            result.append("[BIDI_U+%04X]" % code)
            changed = True
        elif (code < 32 and char not in "\n\t") or code == 127:
            result.append("[C0_U+%04X]" % code)
            changed = True
        else:
            result.append(char)
    return "".join(result), changed


def sanitize(raw, max_bytes, max_lines):
    text, invalid = _decode(raw[: max_bytes + 1])
    truncated = len(raw) > max_bytes
    if truncated:
        text = raw[:max_bytes].decode("utf-8", errors="ignore")
    text, redacted = _redact(text)
    text, controls = _neutralize_controls(text)
    lines = text.splitlines(keepends=True)
    if len(lines) > max_lines:
        text = "".join(lines[:max_lines])
        truncated = True
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        text = encoded[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True
    return {
        "text": text,
        "sanitized_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes_observed": len(raw),
        "lines_observed": len(lines),
        "redacted": redacted,
        "truncated": truncated,
        "control_sequences_neutralized": controls,
        "invalid_text_replaced": invalid,
        "limitations": ["pattern-based redaction is conservative and not a guarantee of secret removal"],
    }


def main():
    parser = argparse.ArgumentParser(description="sanitize evidence from stdin")
    parser.add_argument("--max-bytes", type=int, default=65536)
    parser.add_argument("--max-lines", type=int, default=1000)
    args = parser.parse_args()
    if args.max_bytes < 1 or args.max_lines < 1:
        raise SystemExit(2)
    result = sanitize(sys.stdin.buffer.read(args.max_bytes + 1), args.max_bytes, args.max_lines)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
