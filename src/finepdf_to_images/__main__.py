"""Allow ``python -m finepdf_to_images`` so the CLI is reachable without an installed script."""

from __future__ import annotations

from finepdf_to_images.cli import main

if __name__ == "__main__":
    main()
