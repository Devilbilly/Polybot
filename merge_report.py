#!/usr/bin/env python3
"""Stitch the past-hour analysis fragment(s) into the base PnL HTML report.

    python3 merge_report.py BASE.html OUT.html FRAG1.html [FRAG2.html ...]

Each fragment is inserted (in order) just before </body> of the base report, so the
combined file is one valid HTML doc: PnL report first, then the appended analysis.
"""
import sys


def main():
    base_path, out_path, *frags = sys.argv[1:]
    base = open(base_path, encoding="utf-8").read()
    blocks = []
    for f in frags:
        try:
            blocks.append(open(f, encoding="utf-8").read())
        except FileNotFoundError:
            pass
    inject = "\n".join(blocks)
    marker = "</body>"
    if marker in base:
        merged = base.replace(marker, inject + "\n" + marker, 1)
    else:                                  # base had no </body> -> just append
        merged = base + "\n" + inject
    open(out_path, "w", encoding="utf-8").write(merged)
    print(f"merged {len(frags)} fragment(s) into {out_path} ({len(merged)} bytes)")


if __name__ == "__main__":
    main()
