#!/usr/bin/env python3
"""
PicoCore V2 - Binary Log Converter
====================================
Converts binary log files produced by the PicoCore V2 Logger on ESP32
into human-readable formats.

Binary record format (variable length, no delimiter):
    [1 byte: level (uint8)] [4 bytes: uptime_ms (uint32 LE)] [N bytes: message (printable ASCII)]

Records are concatenated directly. Boundaries are detected because message
content is always printable ASCII (0x20-0x7E), while level bytes (1-6) are not.

NOTE: Very long messages may appear truncated - this is a logger-side limitation
(the ByteRingBuffer has a fixed total capacity and cuts messages that overflow it).

Usage (CLI):
    python picocore_log_converter.py logs.bin                     # plain text to stdout
    python picocore_log_converter.py logs.bin -f csv -o out.csv
    python picocore_log_converter.py logs.bin -f json -o out.json
    python picocore_log_converter.py logs.bin -f table
    python picocore_log_converter.py logs.bin --min-level WARN    # filter by severity
    python picocore_log_converter.py logs.bin --info              # file summary only

Usage (as a module):
    from picocore_log_converter import parse_log_file, convert

    records = parse_log_file("logs.bin")
    convert(records, fmt="csv", output_path="out.csv")
"""

import struct
import sys
import os
import csv
import json
import argparse
from datetime import timedelta

# ---------------------------------------------------------------------------
# Constants (mirror of MicroPython constants.py)
# ---------------------------------------------------------------------------
LEVEL_NAMES = {
    1: "FATAL",
    2: "ERROR",
    3: "WARN",
    4: "INFO",
    5: "DEBUG",
    6: "TRACE",
}
LEVEL_NAMES_REV = {v: k for k, v in LEVEL_NAMES.items()}
VALID_LEVELS = set(LEVEL_NAMES)

MAX_UPTIME_MS = 30 * 24 * 3600 * 1000  # 30 days sanity cap

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _fmt_uptime(ms: int) -> str:
    """Format millisecond uptime as Dd HH:MM:SS.mmm"""
    total_s, millis = divmod(ms, 1000)
    td = timedelta(seconds=total_s)
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{td.days}d {hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def parse_log_file(path: str, min_level: int = 0) -> list[dict]:
    """
    Parse a PicoCore V2 binary log file.

    Record format: level(1 byte) + uptime_ms(4 bytes LE) + message(variable printable ASCII).
    Records are concatenated with no delimiter. Boundaries are detected because message
    bytes are always printable (0x20-0x7E), while level bytes (1-6) are control characters.

    :param path:      Path to the .bin log file.
    :param min_level: Severity filter. 0 = show all. Pass LEVEL_NAMES_REV['WARN'] = 3
                      to show only WARN / ERROR / FATAL.
    :return:          List of record dicts.
    """
    with open(path, "rb") as f:
        raw = f.read()

    records = []
    i = 0

    while i < len(raw):
        lvl = raw[i]

        # skip bytes that aren't valid level headers
        if lvl not in VALID_LEVELS:
            i += 1
            continue

        if i + 5 > len(raw):
            break

        ts = struct.unpack_from("<I", raw, i + 1)[0]
        if ts > MAX_UPTIME_MS:
            i += 1
            continue

        # consume message: printable ASCII bytes only
        j = i + 5
        while j < len(raw) and (32 <= raw[j] < 127 or raw[j] == ord("\n")):
            j += 1

        msg = raw[i + 5: j].decode("utf-8", errors="replace").strip()
        i = j  # advance; next byte is the level of the next record

        if not msg and ts == 0:
            continue  # skip empty padding records

        if min_level > 0 and lvl > min_level:
            continue  # filtered out

        records.append({
            "index":      len(records),
            "level_int":  lvl,
            "level_name": LEVEL_NAMES[lvl],
            "uptime_ms":  ts,
            "uptime_str": _fmt_uptime(ts),
            "message":    msg,
        })

    return records


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _to_text(records: list[dict]) -> str:
    return "\n".join(
        f"{r['uptime_str']}  {r['level_name']:<5}  {r['message']}"
        for r in records
    )


def _to_csv_str(records: list[dict]) -> str:
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["index", "uptime_ms", "uptime_str", "level_int", "level_name", "message"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue()


def _to_json_str(records: list[dict]) -> str:
    return json.dumps(records, indent=2, ensure_ascii=False)


def _to_table(records: list[dict]) -> str:
    if not records:
        return "(no records)"
    msg_col = min(80, max(len(r["message"]) for r in records))
    ts_col  = max(len(r["uptime_str"]) for r in records)
    idx_col = max(5, len(str(records[-1]["index"])))
    header  = f"  {'#':>{idx_col}}  {'UPTIME':<{ts_col}}  {'LEVEL':<5}  MESSAGE"
    sep     = "  " + "-" * (idx_col + ts_col + msg_col + 14)
    rows = []
    for r in records:
        msg = r["message"] if len(r["message"]) <= msg_col else r["message"][:msg_col - 1] + "…"
        rows.append(f"  {r['index']:>{idx_col}}  {r['uptime_str']:<{ts_col}}  {r['level_name']:<5}  {msg}")
    return "\n".join([sep, header, sep] + rows + [sep])


# ---------------------------------------------------------------------------
# Public convert() entry point
# ---------------------------------------------------------------------------

FORMATS = ("text", "csv", "json", "table")


def convert(records: list[dict], fmt: str = "text", output_path: str | None = None) -> str:
    """
    Convert parsed records to the desired format.

    :param records:     Output of parse_log_file().
    :param fmt:         One of: 'text', 'csv', 'json', 'table'.
    :param output_path: If given, write result to this file path.
    :return:            Formatted string.
    """
    fmt = fmt.lower()
    if fmt == "text":
        out = _to_text(records)
    elif fmt == "csv":
        out = _to_csv_str(records)
    elif fmt == "json":
        out = _to_json_str(records)
    elif fmt == "table":
        out = _to_table(records)
    else:
        raise ValueError(f"Unknown format '{fmt}'. Choose from: {FORMATS}")

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"[picocore_log_converter] {len(records)} records → {output_path}")

    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="picocore_log_converter",
        description="Convert PicoCore V2 binary log files to readable formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python picocore_log_converter.py logs.bin
  python picocore_log_converter.py logs.bin -f csv -o out.csv
  python picocore_log_converter.py logs.bin -f json -o out.json
  python picocore_log_converter.py logs.bin -f table
  python picocore_log_converter.py logs.bin --min-level WARN
  python picocore_log_converter.py logs.bin --info
""",
    )
    p.add_argument("input", help="Path to the .bin log file")
    p.add_argument("-f", "--format", choices=FORMATS, default="text", metavar="FORMAT",
                   help=f"Output format: {', '.join(FORMATS)}  (default: text)")
    p.add_argument("-o", "--output", default=None, metavar="FILE",
                   help="Write output to FILE (default: stdout)")
    p.add_argument("--min-level", default=None, metavar="LEVEL",
                   help=f"Only show this severity and above. Choices: {', '.join(LEVEL_NAMES_REV)}")
    p.add_argument("--info", action="store_true",
                   help="Print file summary and exit without converting")
    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    min_level = 0
    if args.min_level:
        lvl = args.min_level.upper()
        if lvl not in LEVEL_NAMES_REV:
            print(f"Error: unknown level '{args.min_level}'. Choose from: {list(LEVEL_NAMES_REV)}", file=sys.stderr)
            sys.exit(1)
        min_level = LEVEL_NAMES_REV[lvl]

    records = parse_log_file(args.input, min_level=min_level)

    if args.info:
        file_size = os.path.getsize(args.input)
        levels: dict[str, int] = {}
        for r in records:
            levels[r["level_name"]] = levels.get(r["level_name"], 0) + 1
        print(f"File     : {args.input}")
        print(f"Size     : {file_size} bytes")
        print(f"Records  : {len(records)}")
        if levels:
            print(f"By level : {', '.join(f'{k}={v}' for k, v in sorted(levels.items()))}")
            print(f"First    : {records[0]['uptime_str']}  [{records[0]['level_name']}]  {records[0]['message'][:80]}")
            print(f"Last     : {records[-1]['uptime_str']}  [{records[-1]['level_name']}]  {records[-1]['message'][:80]}")
        return

    if not records:
        print("(no records found)", file=sys.stderr)
        sys.exit(0)

    result = convert(records, fmt=args.format, output_path=args.output)
    if not args.output:
        print(result)


if __name__ == "__main__":
    main()