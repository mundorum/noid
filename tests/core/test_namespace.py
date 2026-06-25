"""Tests for noid.core.namespace.NamespaceResolver."""
import importlib
from pathlib import Path

import pytest
import yaml

from noid.core.namespace import NamespaceResolver


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_ns_yaml(tmp_path: Path, namespaces: dict) -> Path:
    p = tmp_path / "noid-namespaces.yaml"
    p.write_text(yaml.dump({"namespaces": namespaces}))
    return p


# ── is_namespaced ─────────────────────────────────────────────────────────────

def test_is_namespaced_returns_false_without_colon():
    r = NamespaceResolver()
    assert not r.is_namespaced("plain_module_path")


def test_is_namespaced_returns_false_for_unknown_prefix():
    r = NamespaceResolver()
    r.load_from_dict({"noid": {"kind": "module", "root": "noid_collections"}})
    assert not r.is_namespaced("other:thing")


def test_is_namespaced_returns_true_for_known_prefix():
    r = NamespaceResolver()
    r.load_from_dict({"noid": {"kind": "module", "root": "noid_collections"}})
    assert r.is_namespaced("noid:data.text_source")


def test_is_namespaced_rejects_empty_prefix():
    r = NamespaceResolver()
    assert not r.is_namespaced(":memory:")


# ── resolve_module ────────────────────────────────────────────────────────────

def test_resolve_module_plain_string_unchanged():
    r = NamespaceResolver()
    assert r.resolve_module("my.module") == "my.module"


def test_resolve_module_with_module_namespace():
    r = NamespaceResolver()
    r.load_from_dict({"noid": {"kind": "module", "root": "noid_collections"}})
    assert r.resolve_module("noid:data.text_source") == "noid_collections.data.text_source"


def test_resolve_module_replaces_hyphens():
    r = NamespaceResolver()
    r.load_from_dict({"noid": {"kind": "module", "root": "noid_collections"}})
    assert r.resolve_module("noid:data.text-source") == "noid_collections.data.text_source"


def test_resolve_module_resource_namespace_unchanged():
    r = NamespaceResolver()
    r.load_from_dict({"shared": {"kind": "resource", "root": "/srv/shared"}})
    assert r.resolve_module("shared:corpus/book.txt") == "shared:corpus/book.txt"


# ── resolve_resource ──────────────────────────────────────────────────────────

def test_resolve_resource_plain_path_unchanged():
    r = NamespaceResolver()
    assert r.resolve_resource("relative/path.txt") == "relative/path.txt"


def test_resolve_resource_with_resource_namespace(tmp_path):
    r = NamespaceResolver()
    r.load_from_dict({"shared": {"kind": "resource", "root": str(tmp_path)}})
    result = r.resolve_resource("shared:docs/book.pdf")
    assert result == str((tmp_path / "docs" / "book.pdf").resolve())


def test_resolve_resource_module_namespace_unchanged():
    r = NamespaceResolver()
    r.load_from_dict({"noid": {"kind": "module", "root": "noid_collections"}})
    assert r.resolve_resource("noid:data.text_source") == "noid:data.text_source"


# ── load_from_file ────────────────────────────────────────────────────────────

def test_load_from_file_module_namespace(tmp_path):
    _make_ns_yaml(tmp_path, {"noid": {"kind": "module", "root": "noid_collections"}})
    r = NamespaceResolver()
    r.load_from_file(tmp_path / "noid-namespaces.yaml")
    assert r.resolve_module("noid:data.text_source") == "noid_collections.data.text_source"


def test_load_from_file_resource_relative_root(tmp_path):
    """Relative 'root' in a YAML file is resolved against the file's directory."""
    _make_ns_yaml(tmp_path, {"shared": {"kind": "resource", "root": "data"}})
    r = NamespaceResolver()
    r.load_from_file(tmp_path / "noid-namespaces.yaml")
    expected = str((tmp_path / "data" / "book.pdf").resolve())
    assert r.resolve_resource("shared:book.pdf") == expected


def test_load_from_file_nonexistent_file_is_no_op():
    r = NamespaceResolver()
    r.load_from_file(Path("/no/such/file.yaml"))
    assert r._ns == {}


# ── discover_and_load ─────────────────────────────────────────────────────────

def test_discover_and_load_walks_up_to_find_ns_file(tmp_path):
    # Put the namespace file in a parent directory
    ns_file = _make_ns_yaml(tmp_path, {"noid": {"kind": "module", "root": "nc"}})
    scene_dir = tmp_path / "scenes" / "demo"
    scene_dir.mkdir(parents=True)
    r = NamespaceResolver()
    r.discover_and_load(scene_dir)
    assert r.resolve_module("noid:data.text_source") == "nc.data.text_source"


def test_discover_and_load_no_ns_file_no_error(tmp_path):
    r = NamespaceResolver()
    r.discover_and_load(tmp_path)
    assert r._ns == {}


def test_discover_and_load_env_var(tmp_path, monkeypatch):
    ns_file = _make_ns_yaml(tmp_path, {"env": {"kind": "module", "root": "env_pkg"}})
    monkeypatch.setenv("NOID_NAMESPACES", str(ns_file))
    r = NamespaceResolver()
    r.discover_and_load(None)
    assert r.is_namespaced("env:something")


# ── inline namespaces override project-level ──────────────────────────────────

def test_inline_namespaces_override_project_level(tmp_path):
    _make_ns_yaml(tmp_path, {"noid": {"kind": "module", "root": "base_pkg"}})
    r = NamespaceResolver()
    r.discover_and_load(tmp_path)
    r.load_from_dict({"noid": {"kind": "module", "root": "override_pkg"}}, tmp_path)
    assert r.resolve_module("noid:data.x") == "override_pkg.data.x"


# ── resource namespace with inline base_dir ────────────────────────────────────

def test_load_from_dict_resource_relative_root(tmp_path):
    r = NamespaceResolver()
    r.load_from_dict({"local": {"kind": "resource", "root": "."}}, base_dir=tmp_path)
    result = r.resolve_resource("local:file.txt")
    assert result == str((tmp_path / "file.txt").resolve())
