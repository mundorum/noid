# noid — Scene Package

A **scene package** is the canonical on-disk representation of a runnable noid
scene. It groups the four resource types that a scene can depend on into a single
directory.

---

## Directory structure

```
<scene-id>/
├── scene.json          required — declarative scene spec
├── components/         optional — Python files with custom component classes
│   └── my_logic.py
└── data/               optional — data files referenced by component properties
    ├── input.csv
    └── docs/
        └── manual.pdf
```

---

## `scene.json`

The declarative spec consumed by `NoidPlayer.load()`.

```json
{
  "title":   "My Scene",

  "namespaces": {
    "local":  {"kind": "resource", "root": "."}
  },

  "imports": [
    "components/my_logic.py",
    "noid_collections.data.text_source"
  ],

  "components": [
    {
      "type":       "data:text-source",
      "id":         "src",
      "properties": {"text": "Hello world"},
      "subscribe":  "player/start~trigger",
      "publish":    "text~pipeline/out;done~player/done"
    }
  ]
}
```

### Paths inside `scene.json`

| Reference type | Example | Resolved by |
|---|---|---|
| Relative Python file in `imports` | `"components/my_logic.py"` | Relative to scene directory |
| Namespace-prefixed module | `"noid:data.text_source"` | `NamespaceResolver` |
| Property value (`kind: resource`) | `"data/input.csv"` | Relative to scene directory (no namespace needed) |
| Property value with resource namespace | `"shared:corpora/book.pdf"` | `NamespaceResolver` → absolute path |

---

## `components/`

Python files placed here are auto-resolvable from `imports` using relative paths.
They register components with `@Noid.component` and are loaded by the player
before instantiating scene components.

Custom components placed here are specific to this scene. Reusable components
belong in a separate `noid-collections`-style package.

---

## `data/`

Data files referenced by component properties (PDFs, CSVs, text files, etc.).
The `data/` directory has no enforced structure — organise it however the scene
needs.

Components declare path properties with `"kind": "resource"` in their spec.
Without a namespace prefix, relative paths are resolved against the scene
directory. With a namespace prefix (e.g. `"shared:..."`) they are resolved by
`NamespaceResolver`.

---

## Loading a scene package

```python
from noid.core.player import NoidPlayer

# Load from directory (discovers scene.json automatically)
NoidPlayer.play("/path/to/my_scene/")

# Equivalent: load from scene.json directly
NoidPlayer.play("/path/to/my_scene/scene.json")
```

From the CLI:
```bash
noid-play /path/to/my_scene/
noid-play /path/to/my_scene/scene.json
```

---

## See also

- [Namespace system](namespaces.md) — how namespace prefixes in imports, types, and properties are resolved
- [Player reference](player.md) — full `NoidPlayer` API
