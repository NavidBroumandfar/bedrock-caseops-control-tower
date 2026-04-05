"""
Final output packaging utility — E-1.

Responsible for serialising a completed CaseOutput to the local filesystem.

Public surface:
  write_case_output(output, output_dir) → Path   — write JSON and return path
  OutputWriteError                               — raised on filesystem errors

Design:
  - Output directory defaults to "outputs" (relative or absolute path accepted)
  - File name is always {document_id}.json so outputs are predictably locatable
  - Parent directory is created if it does not exist
  - Serialisation via Pydantic model_dump_json() for guaranteed schema fidelity
  - No AWS interaction; this is local persistence only
  - output_dir is injectable so tests can write to tmp_path without env mutation
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from app.schemas.output_models import CaseOutput

# Default output base directory; override via the output_dir parameter or
# the OUTPUT_DIR environment variable (read by the CLI, not here).
_DEFAULT_OUTPUT_DIR = "outputs"


class OutputWriteError(Exception):
    """Raised when the final CaseOutput cannot be written to the filesystem."""


def write_case_output(
    output: CaseOutput,
    output_dir: Union[str, Path] = _DEFAULT_OUTPUT_DIR,
) -> Path:
    """
    Write a CaseOutput as formatted JSON to {output_dir}/{document_id}.json.

    Returns the resolved absolute path of the written file.

    Raises OutputWriteError if the directory cannot be created or the file
    cannot be written.  The original OSError is always chained via __cause__.
    """
    base = Path(output_dir)

    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputWriteError(
            f"Cannot create output directory {str(base)!r}: {exc}"
        ) from exc

    dest = base / f"{output.document_id}.json"

    try:
        dest.write_text(
            output.model_dump_json(indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise OutputWriteError(
            f"Cannot write output file {str(dest)!r}: {exc}"
        ) from exc

    return dest.resolve()
