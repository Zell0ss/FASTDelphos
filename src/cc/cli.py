import argparse
import pathlib

from cc.pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="cc",
                                     description="Comprehension Compiler — build a code graph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    comp = sub.add_parser("compile", help="Compile a repo into a navigable graph")
    comp.add_argument("repo", type=pathlib.Path, help="Path to the target repo")
    comp.add_argument("--out", type=pathlib.Path, default=pathlib.Path("cc-out"),
                      help="Output directory (default: cc-out/)")

    args = parser.parse_args()

    if args.cmd == "compile":
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out)
        print(f"Done. Open {args.out}/index.html")


if __name__ == "__main__":
    main()
