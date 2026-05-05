"""Create an EISight experiment session scaffold.

This utility creates empty session folders and copies only CSV header rows from
the relevant metadata templates. It never creates raw/calibrated/QC artifacts or
measurement values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


TOOL_VERSION = "0.1.0"
VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SESSION_DIRS = (
    "captures",
    "listen",
    "combined",
    "reports",
    "plots",
    "metadata",
)

TEMPLATES = {
    "resistor-cal": (
        ("hardware/resistor_inventory.csv", "metadata/resistor_inventory.csv"),
        ("hardware/dmm_inventory.csv", "metadata/dmm_inventory.csv"),
        ("hardware/jumper_state.csv", "metadata/jumper_state.csv"),
        ("hardware/dc_bias_check.csv", "metadata/dc_bias_check.csv"),
        ("hardware/bench_session_log.csv", "metadata/bench_session_log.csv"),
        ("hardware/rfb_inventory.csv", "metadata/rfb_inventory.csv"),
        ("hardware/power_rail_check.csv", "metadata/power_rail_check.csv"),
        ("hardware/i2c_sanity_log.csv", "metadata/i2c_sanity_log.csv"),
    ),
    "milk-water": (
        ("metadata/sample_inventory.csv", "metadata/sample_inventory.csv"),
        ("metadata/milk_session_log.csv", "metadata/milk_session_log.csv"),
        ("metadata/dilution_plan.csv", "metadata/dilution_plan.csv"),
        ("metadata/lactometer_log.csv", "metadata/lactometer_log.csv"),
        ("metadata/ph_log.csv", "metadata/ph_log.csv"),
        ("metadata/temperature_log.csv", "metadata/temperature_log.csv"),
        ("metadata/dataset_split_plan.csv", "metadata/dataset_split_plan.csv"),
        ("hardware/cell_geometry.csv", "metadata/cell_geometry.csv"),
        ("hardware/dc_bias_check.csv", "metadata/dc_bias_check.csv"),
        ("hardware/bench_session_log.csv", "metadata/bench_session_log.csv"),
    ),
}


class SessionCreateError(Exception):
    """Raised for user-facing scaffold creation failures."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _validate_session_id(session_id: str) -> str:
    if session_id == "":
        raise SessionCreateError("invalid session_id: must not be empty")
    if len(session_id) > 64:
        raise SessionCreateError("invalid session_id: length must be 1 to 64")
    if "/" in session_id or "\\" in session_id:
        raise SessionCreateError("invalid session_id: slashes are not allowed")
    if ".." in session_id:
        raise SessionCreateError("invalid session_id: '..' is not allowed")
    if (
        Path(session_id).is_absolute()
        or PureWindowsPath(session_id).is_absolute()
        or PurePosixPath(session_id).is_absolute()
    ):
        raise SessionCreateError("invalid session_id: absolute paths are not allowed")
    if not VALID_SESSION_ID.fullmatch(session_id):
        raise SessionCreateError(
            "invalid session_id: use only letters, numbers, underscore, and hyphen"
        )
    return session_id


def _is_non_empty(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def _read_header(source_path: Path) -> bytes:
    if not source_path.exists():
        raise SessionCreateError(f"missing source template: {source_path}")
    if not source_path.is_file():
        raise SessionCreateError(f"source template is not a file: {source_path}")
    with source_path.open("rb") as handle:
        header = handle.readline()
    if not header:
        raise SessionCreateError(f"source template is empty: {source_path}")
    return header


def _load_template_headers(
    repo_root: Path, kind: str
) -> list[tuple[Path, Path, bytes, str]]:
    if kind not in TEMPLATES:
        raise SessionCreateError(f"unsupported session kind: {kind}")
    headers = []
    for source_rel, dest_rel in TEMPLATES[kind]:
        source_path = repo_root / source_rel
        header = _read_header(source_path)
        headers.append((
            source_path,
            Path(dest_rel),
            header,
            hashlib.sha256(header).hexdigest(),
        ))
    return headers


def _has_non_empty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _write_header(path: Path, header: bytes) -> None:
    if path.exists() and not path.is_file():
        raise SessionCreateError(f"template destination is not a file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(header)


def _source_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        return None
    return commit


def _created_utc() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def create_session(
    *,
    session_id: str,
    kind: str,
    output_root: Path,
    operator: str | None,
    force: bool,
    overwrite_templates: bool,
) -> Path:
    repo_root = _repo_root()
    session_id = _validate_session_id(session_id)
    template_headers = _load_template_headers(repo_root, kind)

    session_dir = output_root / session_id
    if output_root.exists() and not output_root.is_dir():
        raise SessionCreateError(f"output root exists and is not a directory: {output_root}")
    if session_dir.exists() and not session_dir.is_dir():
        raise SessionCreateError(f"session path exists and is not a directory: {session_dir}")
    if _is_non_empty(session_dir) and not force:
        raise SessionCreateError(
            f"session already exists and is non-empty: {session_dir}; use --force"
        )

    output_root.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    for dirname in SESSION_DIRS:
        (session_dir / dirname).mkdir(exist_ok=True)

    templates_copied: list[str] = []
    templates_skipped: list[str] = []
    template_header_sha256: dict[str, str] = {}

    for _source_path, dest_rel, header, header_sha in template_headers:
        dest_path = session_dir / dest_rel
        dest_key = dest_rel.as_posix()
        template_header_sha256[dest_key] = header_sha
        if _has_non_empty_file(dest_path) and not overwrite_templates:
            templates_skipped.append(dest_key)
            continue
        _write_header(dest_path, header)
        templates_copied.append(dest_key)

    session_info = {
        "session_id": session_id,
        "kind": kind,
        "created_utc": _created_utc(),
        "created_by_operator": operator or "",
        "source_commit": _source_commit(repo_root),
        "tool_version": TOOL_VERSION,
        "templates_copied": templates_copied,
        "templates_skipped": templates_skipped,
        "template_header_sha256": template_header_sha256,
        "notes": (
            "Scaffold only; no measurement values or generated raw, calibrated, "
            "QC, trusted-band, report, or plot artifacts were created."
        ),
    }
    info_path = session_dir / "SESSION_INFO.json"
    info_path.write_text(json.dumps(session_info, indent=2) + "\n", encoding="utf-8")
    return session_dir


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an EISight session scaffold under data/real/<SESSION_ID>."
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--kind", required=True, choices=sorted(TEMPLATES))
    parser.add_argument("--output-root", default="data/real")
    parser.add_argument("--operator")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--overwrite-templates", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        session_dir = create_session(
            session_id=args.session_id,
            kind=args.kind,
            output_root=Path(args.output_root),
            operator=args.operator,
            force=args.force,
            overwrite_templates=args.overwrite_templates,
        )
    except SessionCreateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created session scaffold: {session_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
