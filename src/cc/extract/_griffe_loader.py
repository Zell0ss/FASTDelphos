"""GriffeLoader tolerant of re-export aliases that shadow a real subpackage
directory of the same name.

Pattern (seen in illumiows): a regular package's `__init__.py` re-exports
a submodule under an alias sharing the submodule's own directory name —
`from api.public.workload import views as workload`. When griffe later tries
to descend into `workload/`, it looks up the parent module's `workload`
member, finds the alias instead of a real module, and accessing
`is_namespace_package` on that alias forces resolution of its target — which
is inside the very directory griffe is trying to load, raising
`CyclicAliasError`/`AliasResolutionError` and aborting the ENTIRE package
load. See `doc_proyecto/` for the full diagnosis.

This subclass scrubs such shadowing aliases from the tree the moment they'd
be looked up as a parent, replacing them with the real namespace module the
filesystem says should be there. No information is lost: the AST import
table built independently by `_calls_resolver.py`/`calls.py` already captures
the same re-export. It also contains any residual per-module load failure
(shadow-related or not) so one broken module never takes down the whole
package — failures are recorded in `module_load_failures` instead.

Depends on GriffeLoader internals not covered by griffe's public API
contract — pinned via `pyproject.toml` (see spec). The
`test_shadow_tolerant_loader_*` fixtures in `tests/test_griffe_loader.py` are
the canary: if a griffe upgrade changes this behavior, those tests fail
before any real repo does.
"""

import pathlib

import griffe


class ShadowTolerantLoader(griffe.GriffeLoader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # (parent_qualname, alias_name, target_path)
        self.scrubbed: list[tuple[str, str, str]] = []
        # (module_qualname, location, error)
        self.module_load_failures: list[tuple[str, str, str]] = []

    def _get_or_create_parent_module(self, module, subparts, subpath):
        parent_parts = subparts[:-1]
        if not parent_parts:
            return module
        parent_module = module
        parents = list(subpath.parents)
        if subpath.stem == "__init__":
            parents.pop(0)
        for parent_offset, parent_part in enumerate(parent_parts, 2):
            module_filepath = parents[len(subparts) - parent_offset]
            try:
                member = parent_module.get_member(parent_part)
            except KeyError as error:
                if parent_module.is_namespace_package or parent_module.is_namespace_subpackage:
                    next_parent_module = self._create_module(parent_part, [module_filepath])
                    parent_module.set_member(parent_part, next_parent_module)
                    parent_module = next_parent_module
                else:
                    raise griffe.UnimportableModuleError(
                        f"Skip {subpath}, it is not importable"
                    ) from error
                continue
            if member.is_alias:
                self.scrubbed.append(
                    (parent_module.path, parent_part, str(getattr(member, "target_path", "?")))
                )
                del parent_module.members[parent_part]
                next_parent_module = self._create_module(parent_part, [module_filepath])
                parent_module.set_member(parent_part, next_parent_module)
                parent_module = next_parent_module
                continue
            parent_module = member
            parent_namespace = (
                parent_module.is_namespace_package or parent_module.is_namespace_subpackage
            )
            if parent_namespace and module_filepath not in parent_module.filepath:
                parent_module.filepath.append(module_filepath)
        return parent_module

    def _load_submodule(self, module, subparts, subpath):
        for subpart in subparts:
            if "." in subpart:
                return
        qualname = ".".join((module.path, *subparts))
        try:
            parent_module = self._get_or_create_parent_module(module, subparts, subpath)
        except griffe.UnimportableModuleError as error:
            self.module_load_failures.append((qualname, str(subpath), str(error)))
            return
        submodule_name = subparts[-1]
        try:
            submodule = self._load_module(
                submodule_name, subpath, submodules=False, parent=parent_module
            )
        except griffe.GriffeError as error:
            self.module_load_failures.append((qualname, str(subpath), str(error)))
            return
        parent_module.set_member(submodule_name, submodule)


def load_tolerant(
    pkg_name: str, search_paths: list[pathlib.Path]
) -> tuple["griffe.Object", list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Load `pkg_name` via `ShadowTolerantLoader`. Returns (root_object, scrubbed,
    module_load_failures) — see `ShadowTolerantLoader` for field shapes."""
    loader = ShadowTolerantLoader(search_paths=search_paths)
    obj = loader.load(pkg_name)
    return obj, loader.scrubbed, loader.module_load_failures
