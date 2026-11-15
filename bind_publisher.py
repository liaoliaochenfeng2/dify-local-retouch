#!/usr/bin/env python3
"""Bind release metadata to a confirmed publisher. This script never publishes.

Run only after the GitHub account and public source repository have been
confirmed. A final package must be rebuilt and validated after this change.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlparse

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author", required=True, help="Confirmed publisher namespace")
    parser.add_argument("--repository", required=True, help="Existing public GitHub repository URL")
    parser.add_argument("--contact-email", required=True, help="Monitored email approved for public publication")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.author):
        parser.error("Author must be a lowercase publisher namespace, not a display name")
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", args.contact_email):
        parser.error("Contact must be a valid monitored public email address")
    repo = args.repository.rstrip("/")
    parsed = urlparse(repo)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or
            not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", parsed.path) or
            parsed.query or parsed.fragment):
        parser.error("Repository must be an exact HTTPS GitHub repository URL")
    root = Path(__file__).resolve().parent
    plugin = root / "plugin"
    manifest_path = plugin / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    created_at = manifest.get("created_at")
    if hasattr(created_at, "isoformat"):
        manifest["created_at"] = created_at.isoformat().replace("+00:00", "Z")
    manifest["author"] = args.author
    manifest["repo"] = repo
    manifest["contact"] = args.contact_email
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    for relative in ("provider/local_retouch.yaml", "tools/prepare.yaml", "tools/retouch.yaml"):
        path = plugin / relative
        content = yaml.safe_load(path.read_text())
        content["identity"]["author"] = args.author
        path.write_text(yaml.safe_dump(content, sort_keys=False, allow_unicode=True))
    # Only release-owned docs are changed; research and test logs stay untouched.
    documents = list(root.glob("*.md")) + list(plugin.glob("*.md"))
    documents.extend((plugin / "readme").glob("*.md"))
    for path in documents:
        text = path.read_text()
        text = text.replace("REPLACE_WITH_PUBLIC_REPOSITORY", repo)
        text = text.replace("REPLACE_WITH_PUBLISHER", args.author)
        text = text.replace("REPLACE_WITH_CONTACT_EMAIL", args.contact_email)
        path.write_text(text)
    print("Publisher metadata bound; rebuild and revalidate before submission.")
    print("This script did not create a repository or submit a Marketplace PR.")


if __name__ == "__main__":
    main()
