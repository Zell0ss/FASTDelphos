import argparse
import pathlib

from cc.annotate import run_annotate
from cc.pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cc", description="Comprehension Compiler — build a code graph"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    comp = sub.add_parser("compile", help="Compile a repo into a navigable graph")
    comp.add_argument("repo", type=pathlib.Path, help="Path to the target repo")
    comp.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("cc-out"),
        help="Output directory (default: cc-out/)",
    )
    comp.add_argument(
        "--oracle",
        action="store_true",
        help="Compare static extraction vs. runtime introspection "
        "(only for repos that boot without infra)",
    )
    comp.add_argument(
        "--serve",
        action="store_true",
        help="Serve --out over HTTP on 127.0.0.1 after compiling (Ctrl+C to stop) "
        "— for viewing via an SSH port forward (e.g. VS Code Remote-SSH)",
    )
    comp.add_argument(
        "--port",
        type=int,
        default=8642,
        help="Port for --serve (default: 8642)",
    )
    comp.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help="Glob pattern (relative to the repo root) to exclude from the graph, "
        "e.g. --exclude 'backend/tests/**'. Repeatable.",
    )

    ann = sub.add_parser("annotate", help="Generate LLM why-notes overlay for a compiled graph")
    ann.add_argument(
        "out",
        type=pathlib.Path,
        help="Path to a compiled output directory (from `cc compile --out`)",
    )
    ann.add_argument(
        "--all",
        action="store_true",
        help="Annotate every node, not just the default role-based scope",
    )
    ann.add_argument(
        "--node", metavar="NODE_ID", help="Annotate only this single node id (on-demand)"
    )
    ann.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if hash and prompt_version already match",
    )

    args = parser.parse_args()

    if args.cmd == "compile":
        exclude_patterns = tuple(args.exclude or ())
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out, exclude_patterns=exclude_patterns)
        print(f"Done. Open {args.out}/index.html")
        if args.oracle:
            import sys

            from cc.extract.endpoints import extract_endpoints
            from cc.oracle import compare_oracle

            ep_nodes, _ = extract_endpoints(args.repo, exclude_patterns)
            sys.path.insert(0, str(args.repo.parent))
            try:
                result = compare_oracle(args.repo, ep_nodes)
            finally:
                try:
                    sys.path.remove(str(args.repo.parent))
                except ValueError:
                    pass
            print(
                f"Route recovery: {result['static_count']}/{result['oracle_count']} "
                f"({result['recovery_rate']:.0%})"
            )
            if result.get("missing"):
                print("Missing from static:", result["missing"])
        if args.serve:
            from cc.serve import serve_directory

            serve_directory(args.out, args.port)

    elif args.cmd == "annotate":
        from cc.llm.config import LLMConfigError, load_config

        try:
            config = load_config()
        except LLMConfigError as exc:
            print(f"Config error: {exc}")
            return

        if config.provider == "anthropic":
            from cc.llm.anthropic_adapter import AnthropicClient

            client = AnthropicClient(config)
        else:
            print(f"Provider {config.provider!r} is not implemented yet.")
            return

        report = run_annotate(
            args.out,
            client,
            model_name=config.model,
            extra_instructions=config.extra_instructions,
            node_id=args.node,
            all_nodes=args.all,
            force=args.force,
            threshold=config.orchestrator_threshold,
        )
        print(
            f"Generadas: {report['generated']}, Cacheadas: {report['cached']}, "
            f"Falladas: {report['failed']}"
        )
        if report["failed_ids"]:
            print("Nodos fallados:", ", ".join(report["failed_ids"]))


if __name__ == "__main__":
    main()
