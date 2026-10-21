"""Fetch three.js (r160, ES module build) next to index.html.

three.module.js is not committed (1.2 MB). Run once per clone, or copy the file from
robotics/scripts/vr/webxr_koch_viewer/three.module.js. The headset needs no internet:
everything is served from the Mac.

  python setup_assets.py            # skip if present
  python setup_assets.py --force    # re-download
"""

import argparse
import sys
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent / "three.module.js"
URL = "https://unpkg.com/three@0.160.0/build/three.module.js"
MIN_BYTES = 500_000


def main():
    """Download the module unless it is already there."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if DEST.exists() and not args.force:
        print(f"OK: 既に存在 {DEST} ({DEST.stat().st_size:,} bytes)")
        return
    print(f"取得中: {URL}")
    urllib.request.urlretrieve(URL, DEST)
    size = DEST.stat().st_size
    if size < MIN_BYTES:
        sys.exit(f"取得サイズが小さすぎます({size}B) — ネットワーク/プロキシを確認")
    print(f"OK: {DEST} ({size:,} bytes)")


if __name__ == "__main__":
    main()
