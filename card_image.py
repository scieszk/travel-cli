"""Export a local structured travel or rail result as a PNG, without fetching data."""
import argparse
import json
from pathlib import Path
import sys


MAX_INPUT_BYTES = 4 * 1024 * 1024


class ImageInputError(Exception):
    def __init__(self, code, message, status=2):
        super().__init__(message)
        self.code = code
        self.status = status


class ImageArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's original message may contain private paths or input values.
        raise ImageInputError("INVALID_ARGUMENT", "Invalid image arguments; see --help.")


def _reject_constant(value):
    raise ValueError("Non-finite JSON values are not supported")


def read_input(path):
    """Read no more than the input limit plus one byte, including for stdin."""
    try:
        if path == "-":
            stream = getattr(sys.stdin, "buffer", sys.stdin)
            raw = stream.read(MAX_INPUT_BYTES + 1)
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
        else:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
    except (OSError, ValueError, UnicodeError):
        raise ImageInputError("INPUT_ERROR", "Unable to read the input JSON.") from None
    if len(raw) > MAX_INPUT_BYTES:
        raise ImageInputError("INPUT_TOO_LARGE", "Input JSON must be no larger than 4 MiB.")
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ImageInputError("INVALID_INPUT", "Input must contain valid UTF-8 JSON.") from None


def _valid_shape(kind, data):
    if not isinstance(data, dict):
        return False
    if kind == "rail":
        return all(isinstance(data.get(key), dict) for key in ("outbound", "return"))
    names, rows = (("place",), "days") if kind == "weather" else (("origin", "destination"), "samples")
    return (all(isinstance(data.get(key), str) and data[key].strip() for key in names)
            and isinstance(data.get(rows), list)
            and all(isinstance(row, dict) for row in data[rows]))


def select_data(payload, kind, selection):
    """Select a one-based plan and retain its source/query metadata on a copy."""
    envelope = {}
    data = payload
    if isinstance(payload, dict) and "ok" in payload:
        if payload["ok"] is False:
            raise ImageInputError("UPSTREAM_ERROR", "The input query did not succeed.")
        if payload["ok"] is not True or "data" not in payload:
            raise ImageInputError("INVALID_INPUT", "Input response has an invalid structure.")
        envelope, data = payload, payload["data"]

    container = data if isinstance(data, dict) else {}
    choices = None
    if kind in ("route", "rail") and "plans" in container:
        if not isinstance(container["plans"], list):
            raise ImageInputError("INVALID_INPUT", "Plans must be a JSON array.")
        choices = container["plans"]
    elif kind == "rail" and isinstance(data, list):
        choices = data
    if choices is not None:
        if not 1 <= selection <= len(choices):
            raise ImageInputError("INVALID_SELECTION", "The selected plan is unavailable.")
        data = choices[selection - 1]
    elif selection != 1:
        raise ImageInputError("INVALID_SELECTION", "This input contains only one card.")
    if not _valid_shape(kind, data):
        raise ImageInputError("INVALID_INPUT", "Input does not match the requested card kind.")

    selected = dict(data)
    # A plan's own metadata takes precedence over its collection and envelope.
    for parent in (container, envelope):
        for key, value in parent.items():
            if key == "queried_at" or key == "source" or key.startswith("source_") or key == "sources":
                selected.setdefault(key, value)
    queried_at = selected.get("queried_at")
    if queried_at is not None and not isinstance(queried_at, str):
        raise ImageInputError("INVALID_INPUT", "Query time must be a string when provided.")
    return selected, queried_at


def main(argv=None):
    parser = ImageArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--kind", required=True, choices=("weather", "route", "rail"))
    parser.add_argument("--input", required=True, metavar="FILE_OR_-", help="UTF-8 JSON file, or - for stdin")
    parser.add_argument("--output", required=True, metavar="FILE.png", help="Destination PNG file")
    parser.add_argument("--select", type=int, default=1, metavar="N", help="One-based plan number (default: 1)")
    parser.add_argument("--font", metavar="FONT_FILE", help="Explicit font file for the renderer")
    result = {"schema_version": "1.0", "ok": True, "command": "image"}
    status = 0
    try:
        args = parser.parse_args(argv)
        if args.select < 1 or Path(args.output).suffix.lower() != ".png":
            raise ImageInputError("INVALID_ARGUMENT", "Use a positive --select number and a .png output path.")
        data, queried_at = select_data(read_input(args.input), args.kind, args.select)
        # The optional renderer/Pillow dependency is loaded only for a valid export.
        from image_cards import render_card
        metadata = render_card(args.kind, data, args.output, font_path=args.font, generated_at=queried_at)
        result["data"] = {"image": metadata}
        serialized = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except ImageInputError as error:
        status = error.status
        result.update(ok=False, error={"code": error.code, "message": str(error)})
        serialized = json.dumps(result, ensure_ascii=True)
    except Exception:
        # Dependency, filesystem, font and renderer failures must not reveal input
        # content or private filesystem paths through an exception or traceback.
        status = 1
        result = {"schema_version": "1.0", "ok": False, "command": "image",
                  "error": {"code": "IMAGE_ERROR", "message": "Unable to export PNG; check Pillow, the font, and the output path."}}
        serialized = json.dumps(result, ensure_ascii=True)
    print(serialized)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
