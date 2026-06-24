"""
noid component metadata extractor.

Reads the enriched @Noid.component spec and produces a structured metadata
dict (and YAML file) suitable for use by composition tools.  No access to
the Python source is required at tool time — tools consume the .meta.yaml file.

Spec fields used for metadata (all optional, all ignored by the runtime):
    "name"          — human-readable component name
    "description"   — component description
    properties[n]["description"]          — per-property description
    receive[n]["description"]             — per-input-notice description
    "output_notices": {n: {"description": ...}} — output notice descriptions

CLI:
    noid-extract-meta path/to/component.py
    noid-extract-meta path/to/component.py --out path/to/output.meta.yaml
    noid-extract-meta path/to/component.py --stdout

Programmatic:
    from noid.core.meta import extract_meta, meta_to_yaml
    meta = extract_meta("data:text-source")   # component must be registered first
    print(meta_to_yaml(meta))
"""
import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_meta(component_id: str) -> Dict[str, Any]:
    """
    Build a metadata dict for a registered component.

    The component must already be registered (via @Noid.component or
    Noid.register) before calling this function.

    Returns a dict that matches the .meta.yaml schema.
    """
    from noid.core.component import Noid  # late import — avoid circular

    klass = Noid._oid_reg.get(component_id)
    if klass is None:
        raise KeyError(f"Component not found in registry: {component_id!r}")

    spec = getattr(klass, "_spec", {})
    return _build_meta(spec, klass)


def extract_meta_from_module(module_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Import a Python module and extract metadata for every component it registers.

    Uses dynamic import so the module's @Noid.component decorators run and
    register the components.  Returns a list of metadata dicts (one per
    registered component).

    Raises RuntimeError if the module registers no components.
    """
    from noid.core.component import Noid  # late import — avoid circular

    path = Path(module_path).resolve()
    before = set(Noid._oid_reg.keys())

    _import_module_file(path)

    new_ids = sorted(set(Noid._oid_reg.keys()) - before)
    if not new_ids:
        raise RuntimeError(f"No components were registered by: {path}")

    return [extract_meta(cid) for cid in new_ids]


def meta_to_yaml(meta: Union[Dict[str, Any], List[Dict[str, Any]]]) -> str:
    """Serialize a metadata dict (or list of dicts) to a YAML string."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "pyyaml is required for metadata serialisation.\n"
            "Install it with:  pip install pyyaml\n"
            "Or:               pip install 'mundorum-noid[meta]'"
        ) from exc

    return yaml.dump(
        meta,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def write_meta_yaml(meta: Dict[str, Any], output_path: Union[str, Path]) -> None:
    """Serialise a metadata dict and write it to a YAML file."""
    Path(output_path).write_text(meta_to_yaml(meta), encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal — metadata construction
# ---------------------------------------------------------------------------

def _build_meta(spec: Dict[str, Any], klass: type) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}

    meta["id"] = spec["id"]
    meta["name"] = spec.get("name") or _id_to_name(spec["id"])

    description = spec.get("description") or _class_description(klass)
    if description:
        meta["description"] = description

    # properties
    props = spec.get("properties") or {}
    if props:
        meta["properties"] = {}
        for pname, pdef in props.items():
            p: Dict[str, Any] = {}
            if pdef.get("description"):
                p["description"] = pdef["description"]
            if "default" in pdef:
                p["default"] = pdef["default"]
                p["required"] = False
            else:
                p["required"] = True
            if pdef.get("readonly", False):
                p["readonly"] = True
            meta["properties"][pname] = p

    # input_notices (from receive spec)
    receive = spec.get("receive")
    if receive:
        meta["input_notices"] = _extract_receive_meta(receive)

    # output_notices — explicit spec wins; fall back to names from publish mapping
    output_notices_spec = spec.get("output_notices")
    if output_notices_spec:
        meta["output_notices"] = {}
        for nname, nspec in output_notices_spec.items():
            entry: Dict[str, Any] = {}
            if isinstance(nspec, dict) and nspec.get("description"):
                entry["description"] = nspec["description"]
            meta["output_notices"][nname] = entry
    elif spec.get("publish"):
        meta["output_notices"] = {
            notice: {} for notice, _ in _parse_tilde_pairs(spec["publish"])
        }

    # provides — expand interface specs from registry
    if spec.get("provide"):
        meta["provides"] = _extract_provides_meta(spec["provide"])

    # connects — just the interface ids (strip the #instance_id part)
    if spec.get("connect"):
        meta["connects"] = _extract_connects_meta(spec["connect"])

    return meta


def _extract_receive_meta(receive_spec) -> Dict[str, Any]:
    notices: Dict[str, Any] = {}
    if isinstance(receive_spec, list):
        for n in receive_spec:
            notices[n] = {}
    elif isinstance(receive_spec, dict):
        for n, nspec in receive_spec.items():
            entry: Dict[str, Any] = {}
            if isinstance(nspec, dict) and nspec.get("description"):
                entry["description"] = nspec["description"]
            notices[n] = entry
    return notices


def _extract_provides_meta(provide_spec: List[str]) -> List[Dict[str, Any]]:
    from noid.core.component import Noid  # late import — avoid circular

    result = []
    for itf_id in provide_spec:
        itf_spec = Noid.get_interface(itf_id) or {}
        entry: Dict[str, Any] = {"id": itf_id}
        ops = itf_spec.get("operations") or {}
        if ops:
            entry["operations"] = {}
            for op_name, op_spec in ops.items():
                op: Dict[str, Any] = {}
                if isinstance(op_spec, dict) and op_spec.get("description"):
                    op["description"] = op_spec["description"]
                entry["operations"][op_name] = op
        result.append(entry)
    return result


def _extract_connects_meta(connect_spec: Union[str, List[str]]) -> List[str]:
    items = (
        [s.strip() for s in connect_spec.split(";")]
        if isinstance(connect_spec, str)
        else list(connect_spec)
    )
    return [item.strip().split("#")[0].strip() for item in items]


# ---------------------------------------------------------------------------
# Internal — helpers
# ---------------------------------------------------------------------------

def _id_to_name(spec_id: str) -> str:
    """Derive a human-readable name from a component id.

    'data:text-source' → 'Text Source'
    'lm:lm-agent'      → 'LM Agent'
    """
    name_part = spec_id.split(":")[-1] if ":" in spec_id else spec_id
    return " ".join(p.capitalize() for p in re.split(r"[-_]+", name_part))


def _class_description(klass: type) -> Optional[str]:
    """Return the first paragraph of the class docstring, or None."""
    doc = klass.__doc__
    if not doc:
        return None
    cleaned = inspect.cleandoc(doc).strip()
    first_para = cleaned.split("\n\n")[0].strip()
    return first_para or None


def _import_module_file(path: Path) -> None:
    """Execute a Python file by path so its module-level decorators run."""
    spec = importlib.util.spec_from_file_location("_noid_meta_import", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


def _parse_tilde_pairs(spec_str: str) -> List[Tuple[str, str]]:
    """Parse 'a~b;c~d' into [('a', 'b'), ('c', 'd')]."""
    result = []
    for part in spec_str.split(";"):
        part = part.strip()
        if not part:
            continue
        segments = part.split("~", maxsplit=1)
        result.append(
            (segments[0].strip(), segments[1].strip())
            if len(segments) == 2
            else (part, part)
        )
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="noid-extract-meta",
        description=(
            "Extract noid component metadata from a Python module and write "
            "a .meta.yaml file alongside each source file."
        ),
    )
    parser.add_argument(
        "module",
        nargs="+",
        help="Path(s) to component Python module file(s).",
    )
    parser.add_argument(
        "--out", "-o",
        metavar="PATH",
        help=(
            "Output path. A file path is used as-is when processing a single "
            "component; a directory path receives one <id>.meta.yaml per "
            "component. Defaults to <component-id>.meta.yaml alongside each "
            "source module."
        ),
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print YAML to stdout instead of writing files.",
    )
    args = parser.parse_args()

    out_arg = Path(args.out) if args.out else None
    single_module = len(args.module) == 1

    for module_path_str in args.module:
        module_path = Path(module_path_str)
        try:
            meta_list = extract_meta_from_module(module_path)
        except Exception as exc:
            print(f"Error processing {module_path}: {exc}", file=sys.stderr)
            sys.exit(1)

        single_component = len(meta_list) == 1

        for meta in meta_list:
            yaml_str = meta_to_yaml(meta)

            if args.stdout:
                print(yaml_str)
                continue

            if out_arg is not None and out_arg.is_dir():
                comp_slug = meta["id"].replace(":", "-")
                out_path = out_arg / f"{comp_slug}.meta.yaml"
            elif out_arg is not None and single_module and single_component:
                out_path = out_arg
            else:
                comp_slug = meta["id"].replace(":", "-")
                out_path = module_path.parent / f"{comp_slug}.meta.yaml"

            write_meta_yaml(meta, out_path)
            print(f"Written: {out_path}")


if __name__ == "__main__":
    _cli()
