#!/usr/bin/env python3
"""Run fixture-based tests without access to GitHub's job summary/output files."""
import argparse
import os
from pathlib import Path
import unittest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-directory',type=Path,default=Path(__file__).resolve().parent/'tests')
    args=parser.parse_args()
    # Production helpers legitimately write these files. Synthetic test calls must
    # not append fixture dates/hashes or set outputs for later workflow steps.
    os.environ.pop('GITHUB_STEP_SUMMARY',None)
    os.environ.pop('GITHUB_OUTPUT',None)
    suite=unittest.defaultTestLoader.discover(str(args.start_directory))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
