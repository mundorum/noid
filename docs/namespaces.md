# noid — Namespace System

Namespaces let scenes reference Python modules and data files with logical
short names instead of hard-coded absolute paths. This makes scene packages
portable across machines and deployments.

---

## Two namespace kinds

### `module` — Python import root

Maps a short prefix to a Python package root. Used in `imports` entries and
optionally in component `type` IDs.

```yaml
namespaces:
  noid:
    kind: module
    root: "noid_collections"
```

A scene can then write:

```json
"imports": ["noid:data.text_source"]
```

instead of:

```json
"imports": ["noid_collections.data.text_source"]
```

Hyphens in the suffix are converted to underscores for Python compatibility:
`noid:data.text-source` → `noid_collections.data.text_source`.

### `resource` — filesystem root

Maps a short prefix to a filesystem directory. Used for file-path property
values in components that declare `"kind": "resource"` on the property spec.

```yaml
namespaces:
  shared:
    kind: resource
    root: "/srv/noid/shared"   # absolute path
  local:
    kind: resource
    root: "."                  # relative to the namespace file's directory
```

A scene can then write:

```json
"properties": {
  "input_file": "shared:corpora/ulysses.pdf"
}
```

The player resolves this to an absolute path before passing it to the component.

---

## Namespace file format

`noid-namespaces.yaml` (the default filename):

```yaml
version: "1"

namespaces:
  noid:
    kind: module
    root: "noid_collections"
    description: "Standard noid component collections"   # optional, ignored at runtime

  shared:
    kind: resource
    root: "/srv/noid/shared"

  local:
    kind: resource
    root: "."                  # resolved relative to this YAML file's directory
```

---

## Resolution order

Namespace definitions are merged in this order; **later definitions override earlier ones**:

1. `~/.config/noid/namespaces.yaml` — user-level baseline
2. `noid-namespaces.yaml` found by walking up from the scene directory (first match wins)
3. The path in the `NOID_NAMESPACES` environment variable
4. The `"namespaces"` dict inline in `scene.json` — **highest priority**

Inline namespaces are useful for per-scene overrides without touching project files.

---

## Using namespaces in `imports`

Both plain and namespace-prefixed entries are accepted:

```json
"imports": [
  "noid_collections.data.text_source",    // explicit module path (unchanged)
  "noid:data.text_source",                // resolved via module namespace
  "./custom_component.py"                 // relative file path (unchanged)
]
```

---

## Using namespaces in component `type`

When a component `type` uses a module-namespace prefix, the player auto-imports
the resolved module and maps the newly registered component ID:

```json
{
  "type": "noid:data.text_source",
  "properties": { "text": "Hello" }
}
```

The player:
1. Resolves `noid:data.text_source` → `noid_collections.data.text_source`
2. Imports the module (which calls `@Noid.component`)
3. Discovers that `"data:text-source"` was registered
4. Instantiates `"data:text-source"`

> **Important**: This only works if the module registers exactly one component.
> For modules with multiple components, import them explicitly in `imports` and
> use the full registry ID in `type`.

---

## Opt-in resource resolution for component properties

Resource namespaces only apply to properties that the component author explicitly
marks as file paths in the spec:

```python
@Noid.component({
    "id": "pdf:extractor-pymupdf",
    "properties": {
        "input_file": {
            "default": "",
            "description": "Path to the PDF to extract.",
            "kind": "resource",        # ← opts this property in to resolution
        },
    },
    ...
})
```

At runtime the player replaces `"shared:docs/manual.pdf"` with the absolute
path before constructing the component instance. Components that do not declare
`kind: resource` receive the value verbatim.

---

## Example: portable scene package

Project-level `noid-namespaces.yaml`:
```yaml
namespaces:
  noid:
    kind: module
    root: "noid_collections"
  shared:
    kind: resource
    root: "/mnt/noid_shared"
```

`scene.json`:
```json
{
  "title": "PDF extraction",
  "imports": ["noid:pdf.extractor_pymupdf", "noid:data.file_writer"],
  "components": [
    {
      "type": "pdf:extractor-pymupdf",
      "properties": {
        "input_file":  "shared:books/ulysses.pdf",
        "output_mode": "complete"
      },
      "subscribe": "player/start~extract",
      "publish":   "text~pipeline/text;done~pipeline/done"
    },
    {
      "type": "data:file-writer",
      "properties": {
        "output_file": "shared:output/ulysses.txt"
      },
      "subscribe": "pipeline/text~text;pipeline/done~done",
      "publish":   "written~player/done"
    }
  ]
}
```

This scene runs identically on any machine that mounts the shared volume at
`/mnt/noid_shared` — no path editing required.
