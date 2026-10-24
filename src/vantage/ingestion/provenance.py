"""File provenance: hash, version, workspace tags, data date.

A citation in this demo claims four things — *file*, *version*, *page or row*,
and *data date*. Three of them come from here.

The manifest (`data/manifest.yaml`) is the declared truth about each file:
which workspaces may see it, what date its content is current as of, and which
logical source/version it represents. The SHA-256 is computed from the bytes, so
"the file changed" is detectable rather than asserted.

Why a manifest rather than inferring from filenames: workspace membership and
data date are governance facts about a document. They should be reviewable in
git next to the policy, not parsed out of a file name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml


class ManifestError(RuntimeError):
    """Raised when the manifest is missing, malformed, or disagrees with disk."""


@dataclass(frozen=True)
class FileSpec:
    """One declared file, resolved against disk."""

    path: Path
    # Logical source name used in citations. v1 and v2 of a plan share it.
    source: str
    doc_version: str
    workspaces: tuple[str, ...]
    data_date: date | None
    classification: str
    kind: str  # "document" (→ chunks) | "tabular" (→ SQL knowledge base)
    table: str | None  # tabular only: destination table name

    @property
    def label(self) -> str:
        """`source v2` — how the file is named in logs and citations."""
        return f"{self.source} {self.doc_version}"


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_date(value) -> date | None:
    """Accept a YAML date, an ISO string, or nothing."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_manifest(manifest_path: Path, data_root: Path) -> list[FileSpec]:
    """Parse the manifest and resolve every entry against `data_root`.

    Fails loudly on a missing file or a missing workspace tag: an untagged
    document would otherwise be ingested with no workspaces and become
    invisible, which looks like a retrieval bug rather than a tagging mistake.
    """
    if not manifest_path.is_file():
        raise ManifestError(f"manifest not found: {manifest_path} (run `make dataset` first)")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("files") or []
    if not entries:
        raise ManifestError(f"manifest lists no files: {manifest_path}")

    specs: list[FileSpec] = []
    for entry in entries:
        name = entry.get("file")
        if not name:
            raise ManifestError(f"manifest entry has no 'file' key: {entry!r}")
        path = data_root / name
        if not path.is_file():
            raise ManifestError(f"manifest lists {name!r} but it is not on disk at {path}")

        workspaces = tuple(entry.get("workspaces") or ())
        if not workspaces:
            raise ManifestError(f"{name!r} has no workspaces — refusing to ingest an untagged file")

        kind = entry.get("kind") or "document"
        if kind not in ("document", "tabular"):
            raise ManifestError(f"{name!r} has unknown kind {kind!r}")
        if kind == "tabular" and not entry.get("table"):
            raise ManifestError(f"{name!r} is tabular but names no destination table")

        specs.append(
            FileSpec(
                path=path,
                source=entry.get("source") or name,
                doc_version=str(entry.get("version") or "v1"),
                workspaces=workspaces,
                data_date=_as_date(entry.get("data_date")),
                classification=entry.get("classification") or "internal",
                kind=kind,
                table=entry.get("table"),
            )
        )
    return specs
