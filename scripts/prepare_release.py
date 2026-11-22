"""Build an allowlisted source ZIP without Git history, mail, or runtime data."""
import argparse
import json
import zipfile
from pathlib import Path
from check_public import ROOT, public_files, scan

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/jop-public.zip")
    args = parser.parse_args()
    result = scan()
    if result["findings"]:
        raise SystemExit(json.dumps(result, indent=2))
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = public_files()
    if output in files:
        raise SystemExit("Put the release outside public source directories.")
    # Exclusive creation avoids silently replacing a previously reviewed bundle.
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, "jop/" + file.relative_to(ROOT).as_posix())
    print(json.dumps({"file": str(output), "source_files": len(files), "includes_git_history": False}, indent=2))

if __name__ == "__main__":
    main()
