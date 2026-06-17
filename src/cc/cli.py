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
    comp.add_argument("--oracle", action="store_true",
                      help="Compare static extraction vs. runtime introspection "
                           "(only for repos that boot without infra)")

    args = parser.parse_args()

    if args.cmd == "compile":
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out)
        print(f"Done. Open {args.out}/index.html")
        if args.oracle:
            import sys
            from cc.extract.endpoints import extract_endpoints
            from cc.oracle import compare_oracle
            ep_nodes, _ = extract_endpoints(args.repo)
            sys.path.insert(0, str(args.repo.parent))
            try:
                result = compare_oracle(args.repo, ep_nodes)
            finally:
                try:
                    sys.path.remove(str(args.repo.parent))
                except ValueError:
                    pass
            print(f"Route recovery: {result['static_count']}/{result['oracle_count']} "
                  f"({result['recovery_rate']:.0%})")
            if result.get("missing"):
                print("Missing from static:", result["missing"])


if __name__ == "__main__":
    main()
