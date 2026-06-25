"""
NamespaceResolver — logical-to-physical resolution for noid scenes.

Two namespace kinds:
  module   — maps a short prefix to a Python module root (for imports and types)
  resource — maps a short prefix to a filesystem root (for file-path properties)

Definitions are loaded in priority order (later entries override earlier ones):
  1. ~/.config/noid/namespaces.yaml   (user baseline)
  2. noid-namespaces.yaml found by walking up from the scene directory
  3. NOID_NAMESPACES env var path
  4. "namespaces" dict inline in scene.json   (highest priority)

See docs/namespaces.md for the YAML format and resolution rules.
"""
import importlib
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml as _yaml
    _YAML = True
except ImportError:
    _YAML = False

_NS_FILENAME = "noid-namespaces.yaml"
_USER_NS_FILE = Path.home() / ".config" / "noid" / "namespaces.yaml"


class NamespaceResolver:
    """Resolves namespace-prefixed module paths, resource paths, and component types."""

    def __init__(self) -> None:
        self._ns: Dict[str, Dict[str, Any]] = {}
        self._type_cache: Dict[str, str] = {}

    # ── loading ──────────────────────────────────────────────────────────────

    def load_from_file(self, path: Path) -> None:
        """Load and merge namespace definitions from a YAML file."""
        if not _YAML or not path.exists():
            return
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        for prefix, spec in (data.get("namespaces") or {}).items():
            entry = dict(spec)
            if entry.get("kind") == "resource":
                root = Path(entry.get("root", "."))
                if not root.is_absolute():
                    entry["root"] = str((path.parent / root).resolve())
            self._ns[prefix] = entry

    def load_from_dict(
        self,
        namespaces: Dict[str, Any],
        base_dir: Optional[Path] = None,
    ) -> None:
        """Load and merge namespace definitions from a dict (inline scene namespaces)."""
        for prefix, spec in (namespaces or {}).items():
            entry = dict(spec)
            if entry.get("kind") == "resource":
                root = Path(entry.get("root", "."))
                if not root.is_absolute() and base_dir is not None:
                    entry["root"] = str((base_dir / root).resolve())
            self._ns[prefix] = entry

    def discover_and_load(self, scene_dir: Optional[Path]) -> None:
        """
        Load namespace files in standard priority order.

        Call this once after setting self._scene_dir, then call load_from_dict()
        for inline namespaces from the scene data.
        """
        self.load_from_file(_USER_NS_FILE)
        if scene_dir is not None:
            for parent in [scene_dir, *scene_dir.parents]:
                candidate = parent / _NS_FILENAME
                if candidate.exists():
                    self.load_from_file(candidate)
                    break
        env_path = os.environ.get("NOID_NAMESPACES", "")
        if env_path:
            self.load_from_file(Path(env_path))

    # ── resolution ───────────────────────────────────────────────────────────

    def is_namespaced(self, ref: str) -> bool:
        """Return True if ref starts with a known, non-empty namespace prefix."""
        if ":" not in ref:
            return False
        prefix = ref.split(":", 1)[0]
        return bool(prefix) and prefix in self._ns

    def resolve_module(self, ref: str) -> str:
        """
        Resolve a module namespace reference to a full dotted module path.

            noid:data.text_source  →  noid_collections.data.text_source
            noid:data.text-source  →  noid_collections.data.text_source

        Returns ref unchanged if the prefix is not a module namespace.
        """
        if ":" not in ref:
            return ref
        prefix, rest = ref.split(":", 1)
        ns = self._ns.get(prefix, {})
        if ns.get("kind") != "module":
            return ref
        root = ns.get("root", "")
        rest_clean = rest.lstrip(".").replace("-", "_")
        return f"{root}.{rest_clean}" if rest_clean else root

    def resolve_resource(self, ref: str) -> str:
        """
        Resolve a resource namespace reference to an absolute filesystem path.

            shared:corpus/book.txt  →  /central/repository/corpus/book.txt

        Returns ref unchanged if the prefix is not a resource namespace.
        """
        if ":" not in ref:
            return ref
        prefix, rest = ref.split(":", 1)
        ns = self._ns.get(prefix, {})
        if ns.get("kind") != "resource":
            return ref
        root = ns.get("root", "")
        return str((Path(root) / rest.lstrip("/")).resolve())

    def resolve_type(self, type_str: str) -> str:
        """
        Resolve a namespace-prefixed component type to its registry ID.

        When the prefix is a module namespace, the module is auto-imported and
        the newly registered component ID is returned.

            noid:data.text_source  →  imports noid_collections.data.text_source
                                   →  returns "data:text-source"

        Falls back to type_str unchanged if:
        - the prefix is not a known module namespace
        - import fails
        - more than one component is registered by the imported module
        """
        if type_str in self._type_cache:
            return self._type_cache[type_str]
        if not self.is_namespaced(type_str):
            return type_str
        prefix = type_str.split(":", 1)[0]
        if self._ns.get(prefix, {}).get("kind") != "module":
            return type_str
        module_path = self.resolve_module(type_str)
        # Lazy import to avoid circularity at module load time
        from noid.core.component import Noid
        before = set(Noid._oid_reg.keys())
        try:
            importlib.import_module(module_path)
        except ImportError:
            return type_str
        new_ids = list(set(Noid._oid_reg.keys()) - before)
        if len(new_ids) == 1:
            self._type_cache[type_str] = new_ids[0]
            return new_ids[0]
        return type_str
