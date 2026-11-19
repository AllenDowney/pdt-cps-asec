#!/usr/bin/env python3
"""Write the CPS ASEC data in the common format (STANDARD.md §8).

Reads each release's CSV zip in data/raw/<year>/ (asecpubYYcsv.zip) and writes
data/processed/cps-asec-<year>-<table>.parquet for the household, family and
person tables, holding the variables this repo has records for. Each file is
read back and every cell compared with the CSV, so the conversion is lossless.
"""

import sys
from pathlib import Path

try:
    from pdt.readers import read_csv_zip, zip_member
    from pdt.release import data_source, write_release
except ImportError:
    sys.exit("Needs the PublicDataToolkit library: pip install -e ../PublicDataToolkit, "
             "or run through `make data`.")

ROOT = Path(__file__).resolve().parent.parent
MEMBERS = {"household": "hhpub{yy}.csv", "family": "ffpub{yy}.csv", "person": "pppub{yy}.csv"}


def read(release, table, names):
    """The table's CSV inside the release's zip."""
    source = data_source(ROOT, release, table, format="csv")
    path = ROOT / source["path"]
    if not path.exists():
        sys.exit(f"{path} is missing; run `make download`")
    member = zip_member(path, rf"(^|/){MEMBERS[table].format(yy=release[2:])}$")  # 2019 keeps them in a folder
    return read_csv_zip(path, member, names), source["id"]


if __name__ == "__main__":
    write_release(ROOT, read)
