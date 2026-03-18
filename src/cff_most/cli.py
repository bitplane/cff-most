"""Command-line interface for CFF MOST tools."""

import argparse
import sys

from cff_most.cff import decode, encode


def cmd_decode(args):
    with open(args.input, "rb") as f:
        data = f.read()

    result = decode(data)

    if args.output:
        with open(args.output, "wb") as f:
            f.write(result)
    else:
        sys.stdout.buffer.write(result)


def cmd_encode(args):
    with open(args.input, "rb") as f:
        data = f.read()

    result = encode(data)

    if args.output:
        with open(args.output, "wb") as f:
            f.write(result)
    else:
        sys.stdout.buffer.write(result)


def main():
    parser = argparse.ArgumentParser(prog="cff-most", description="CFF MOST file tools")
    sub = parser.add_subparsers(dest="command")

    p_dec = sub.add_parser("decode", help="decode a CFF file")
    p_dec.add_argument("input", help="input CFF file")
    p_dec.add_argument("output", nargs="?", help="output file (default: stdout)")

    p_enc = sub.add_parser("encode", help="encode a file to CFF format")
    p_enc.add_argument("input", help="input file")
    p_enc.add_argument("output", nargs="?", help="output CFF file (default: stdout)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "decode":
        cmd_decode(args)
    elif args.command == "encode":
        cmd_encode(args)
