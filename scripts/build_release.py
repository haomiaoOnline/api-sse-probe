#!/usr/bin/env python3
"""Make a reviewed portable Agent Skill ZIP from TEAM_MANIFEST only."""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from install import ROOT, package_paths

def main(argv=None):
    parser=argparse.ArgumentParser(description="Build public, manifest-checked Skill ZIP")
    parser.add_argument("--output",help="Destination ZIP path (must not exist)")
    args=parser.parse_args(argv)
    skill=(ROOT/"SKILL.md").read_text(encoding="utf8")
    match=re.search(r'(?m)^  version: "([A-Za-z0-9.-]+)"$',skill)
    if not match:parser.error("missing_skill_version")
    version=match.group(1)
    files=package_paths()
    output=Path(args.output).expanduser().resolve() if args.output else (
        ROOT/"artifacts/releases"/("api-sse-probe-v"+version+".zip"))
    if output.suffix!=".zip":parser.error("expected_zip_output")
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():parser.error("release_zip_exists_choose_a_new_path")
    with zipfile.ZipFile(output,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for source,relative in files:
            info=zipfile.ZipInfo("api-sse-probe/"+relative.as_posix(),
                                 date_time=(2020,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o644<<16
            bundle.writestr(info,source.read_bytes())
    sha=hashlib.sha256(output.read_bytes()).hexdigest()
    sidecar=output.with_suffix(".zip.sha256")
    if sidecar.exists():
        output.unlink()
        parser.error("release_checksum_exists_choose_a_new_path")
    sidecar.write_text(sha+"  "+output.name+"\n",encoding="ascii")
    print(json.dumps({"version":version,"zip":str(output),
                      "sha256":sha,"file_count":len(files)},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())