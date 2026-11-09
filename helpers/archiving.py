"""
NovaCore Archive & Multi-Volume Splitting Utilities
Handles auto-splitting large files (>2GB) into standard PKZIP multi-part volumes.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

logger = logging.getLogger("novacore.helpers.archiving")

TELEGRAM_MAX_FILE_BYTES = 2000 * 1024 * 1024  # 2000 MB (Bot API limit)
DEFAULT_SPLIT_CHUNK_MB = 1900                  # ~1.85 GB safe chunk size


def split_file_into_zips(src_file: str, max_chunk_mb: int = DEFAULT_SPLIT_CHUNK_MB) -> List[str]:
    """
    Splits any file into standard multi-volume zip files using 'zip -0 -s <chunk_mb>m'.
    Returns a sorted list of generated part file paths:
    [...z01, ...z02, ..., ...zip]
    """
    if not os.path.exists(src_file):
        raise FileNotFoundError(f"File not found: {src_file}")

    file_dir = os.path.dirname(os.path.abspath(src_file))
    file_name = os.path.basename(src_file)
    base_stem, _ = os.path.splitext(file_name)
    if not base_stem:
        base_stem = "archive"

    zip_base_name = f"{base_stem}.zip"
    zip_base_path = os.path.join(file_dir, zip_base_name)

    # If zip_base_path happens to match src_file, adjust base name
    if zip_base_path == os.path.abspath(src_file):
        zip_base_name = f"{base_stem}_split.zip"
        zip_base_path = os.path.join(file_dir, zip_base_name)

    cmd = ["zip", "-0", "-s", f"{max_chunk_mb}m", zip_base_name, file_name]
    logger.info("Executing split command: %s (cwd=%s)", " ".join(cmd), file_dir)

    proc = subprocess.run(cmd, cwd=file_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("Zip split command failed: %s | stderr: %s", proc.stdout, proc.stderr)
        raise RuntimeError(f"Split archive failed: {proc.stderr or proc.stdout}")

    # Discover generated parts
    split_stem = os.path.splitext(zip_base_name)[0]
    z_parts: List[str] = []
    last_zip: Optional[str] = None

    for fname in os.listdir(file_dir):
        full_p = os.path.join(file_dir, fname)
        if not os.path.isfile(full_p):
            continue

        if fname == zip_base_name:
            last_zip = full_p
        elif fname.startswith(split_stem + ".z") and fname[len(split_stem) + 2:].isdigit():
            z_parts.append(full_p)

    # Sort .z01, .z02, .z03 ...
    z_parts.sort(key=lambda p: int(os.path.splitext(p)[1][2:]))

    if last_zip:
        z_parts.append(last_zip)

    if not z_parts:
        if os.path.exists(zip_base_path):
            return [zip_base_path]
        raise RuntimeError(f"No zip volumes found after splitting {file_name}")

    logger.info("Split produced %d parts for %s", len(z_parts), file_name)
    return z_parts
