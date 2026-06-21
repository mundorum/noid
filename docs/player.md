# noid Player

The **NoidPlayer** is the Python equivalent of a web browser loading an HTML page:
the JSON scene file is the "page", and the player is the "browser".

A scene file declares which components to instantiate, what properties to give them, and how they communicate — using the exact same attribute names (`type`, `id`, `properties`, `publish`, `subscribe`, `connect`) that the JS oid HTML approach uses.

See [architecture.md](architecture.md) for the broader context and [component-model.md](component-model.md) for component API details.

---

## Analogy: HTML page → JSON scene

| HTML element | JSON scene entry |
|---|---|
| `<button-oid label="Start">` | `{"type": "ex:button", "properties": {"label": "Start"}}` |
| `id="presenter"` | `"id": "presenter"` |
| `publish="click~show/message"` | `"publish": "click~show/message"` |
| `subscribe="show/message~display"` | `"subscribe": "show/message~display"` |
| `connect="itf:transfer#presenter"` | `"connect": "itf:transfer#presenter"` |
| `<script src="mycomps.js">` | `"imports": ["./mycomps.py"]` |

---

## JSON scene format

```json
{
  "title":  "Scene title",

  "imports": [
    "./components.py",            // Python file (resolved relative to scene file)
    "my_package.components"       // Python module (importlib.import_module)
  ],

  "interfaces": [
    {"id": "itf:transfer", "operations": {"send": {}}}
  ],

  "register": [
    {
      "id":        "ex:relay",
      "subscribe": "in/raw~forward",
      "publish":   "forward~out/processed"
    }
  ],

  "components": [
    {
      "type":       "ex:timer",
      "id":         "timer1",
      "properties": {"interval": 0.5, "count": 5},
      "publish":    "tick~timer/tick;done~player/done",
      "subscribe":  "control/pause~pause",
      "connect":    "itf:store#db1",
      "threaded":   false
    }
  ]
}
```

### Sections

| Section | Purpose | Python equivalent |
|---|---|---|
| `imports` | Load Python modules before processing the rest | `import` / `importlib` |
| `interfaces` | Register bus interfaces | `Noid.c_interface(spec)` |
| `register` | Register pure-JSON component types | `Noid.register(spec)` |
| `components` | Instantiate components in declaration order | `Noid.create(type, ...)` |

Sections are processed in the order listed above.

### Component entry fields

| Field | Required | Description |
|---|---|---|
| `type` | yes | Registered component id (e.g. `"ex:timer"`) |
| `id` | no | Instance id used for `provide`/`connect` wiring |
| `properties` | no | Dict of property values (overrides spec defaults) |
| `publish` | no | Instance-level publish spec: `"notice~topic;..."` |
| `subscribe` | no | Instance-level subscribe spec: `"topic~notice;..."` |
| `connect` | no | Connect spec: `"itf:name#component_id;..."` |
| `threaded` | no | `true` → `start_in_thread()` called during `load()` |

All string specs use the same semicolon-separated `topic~notice` syntax as the component spec strings documented in [component-model.md](component-model.md).

---

## Imports

### `.py` file path

Resolved relative to the scene file's parent directory:

```json
"imports": ["./components.py", "../shared/utils.py"]
```

The file is loaded with `importlib.util.spec_from_file_location` and executed in its own module scope. It can call `Noid.component()`, `Noid.c_interface()`, etc. to register types.

### Python module path

```json
"imports": ["my_package.components"]
```

Loaded with `importlib.import_module`. The package must already be on `sys.path`.

---

## Pure-JSON relay via `register`

The `register` section lets you define simple relay/transformer components with no Python code. When a component registered this way receives a notice and has no `handle_*` method, the message is automatically forwarded to the mapped publish topic:

```json
"register": [
  {
    "id":        "ex:relay",
    "subscribe": "raw/input~forward",
    "publish":   "forward~processed/output"
  }
],
"components": [
  {"type": "ex:relay"}
]
```

Flow: `raw/input` arrives → notice `forward` dispatched → no handler found → auto-relay → publishes to `processed/output`.

This is a Python extension with no JS equivalent (JS always needs an explicit handler).

---

## The `player/done` stop signal

`NoidPlayer.run()` blocks until the `player/done` topic is published on the bus. Any component can end the session by publishing to it:

```json
{
  "type":    "ex:timer",
  "publish": "tick~timer/tick;done~player/done"
}
```

When the timer's `done` notice fires, `_notify("done", {})` is called, which publishes to `player/done`, which causes `run()` to stop and clean up.

This replaces the implicit "close tab" event from the browser context.

---

## NoidPlayer API

### `NoidPlayer(bus=None)`

Create a player. If `bus` is omitted, a new `Bus()` is created automatically.

### `player.load(scene)`

Load a scene from:
- A `pathlib.Path` or file path string → reads and parses JSON
- A raw JSON string → parses JSON
- A `dict` → used directly

Returns `self` for chaining. Threaded components are started immediately inside `load()`.

### `await player.start()`

Start all non-threaded components in the caller's event loop.

### `await player.stop()`

Stop all components. Threaded components are signalled and joined (5 s timeout each). The component list is cleared.

### `await player.run(timeout=None)`

Convenience method: calls `start()`, then waits for `player/done` (or timeout), then calls `stop()`.

### `NoidPlayer.play(scene, *, timeout=None, bus=None)`

Class-level convenience that wraps `asyncio.run(player.run(...))`. Suitable for CLI scripts:

```python
from noid.core.player import NoidPlayer
NoidPlayer.play("scene.json")
```

---

## Playground examples

All examples live under `playground/learning/03-player/`. Each subdirectory has a `scene.json` (the "page") and a `run.py` (the "browser launch"):

| Example | JS counterpart | Demonstrates |
|---|---|---|
| `01-basic/` | `01-page/01-basic` | Single timer + logger, fully JSON-wired |
| `02-publish-subscribe/` | `01-page/02-publish-subscribe` | Two publishers, two subscribers with wildcard |
| `03-wildcards/` | `01-page/04-publish-subscribe-wildcards` | `#` and `+` wildcard patterns |
| `04-connect/` | `01-page/06-connect` | `itf:transfer` connect wiring |

Shared component types (timer, logger, counter, store, sender) are defined in `playground/learning/03-player/components.py`, loaded via `"imports": ["../components.py"]` in each scene.

---

## Writing a custom scene

### 1. Define your components

```python
# my_scene/components.py
import asyncio
from noid.core.component import Noid, OidComponent

@Noid.component({
    "id": "my:processor",
    "properties": {"threshold": {"default": 0.5}},
    "receive": ["data"],
})
class ProcessorOid(OidComponent):
    async def handle_data(self, notice, message):
        if message["value"] > self.threshold:
            await self._notify("alert", {"value": message["value"]})
```

### 2. Write the scene

```json
{
  "title": "My Pipeline",
  "imports": ["./components.py"],
  "components": [
    {
      "type":    "my:processor",
      "id":      "proc1",
      "properties": {"threshold": 0.8},
      "subscribe":  "sensor/reading~data",
      "publish":    "alert~alerts/high"
    },
    {
      "type":      "ex:logger",
      "subscribe": "alerts/high~log",
      "properties": {"prefix": "[ALERT]"}
    }
  ]
}
```

### 3. Run it

```python
from noid.core.player import NoidPlayer
NoidPlayer.play("my_scene/scene.json")
```

Or from the terminal:

```bash
python -c "from noid.core.player import NoidPlayer; NoidPlayer.play('my_scene/scene.json')"
```
