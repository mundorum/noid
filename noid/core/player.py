"""
NoidPlayer — declarative scene runner.

Analogous to a browser loading an HTML page with <oid> components:
each JSON "component" entry corresponds to an HTML element with the same
attribute names (type, id, properties, publish, subscribe, connect).

JSON scene format
-----------------
{
  "title":      "My Scene",

  "imports": [
    "./components.py",          // Python file, resolved relative to scene file
    "my_package.components"     // Python module (importlib.import_module)
  ],

  "interfaces": [               // Noid.c_interface() calls
    {"id": "itf:transfer", "operations": {"send": {}}}
  ],

  "register": [                 // Noid.register() calls — no custom Python class
    {"id": "ex:relay",
     "subscribe": "in/topic~relay",
     "publish":   "relay~out/topic"}
  ],

  "components": [               // instances, in declaration order
    {
      "type":       "ex:timer",     // required — registered component id
      "id":         "timer1",       // optional — component_instance_id for provide/connect
      "properties": {"interval": 0.5, "count": 5},
      "publish":    "tick~timer/tick;done~player/done",
      "subscribe":  "...",
      "connect":    "itf:store#store1",
      "threaded":   false           // true → start_in_thread() called immediately
    }
  ]
}

Sections are processed in order: imports → interfaces → register → components.
Components with "threaded": true are started during load(); all others are
started by start() / run().

The player monitors the 'player/done' topic on its bus: run() returns when
that topic is published (or when a timeout elapses).
"""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent


class NoidPlayer:
    """Declarative scene runner — loads JSON, instantiates, and runs components."""

    def __init__(self, bus: Optional[Bus] = None) -> None:
        self._bus: Bus = bus if bus is not None else Bus()
        self._components: List[OidComponent] = []
        self._scene_dir: Optional[Path] = None
        self.title: str = ""

    # ------------------------------------------------------------------ loading

    def load(self, scene: Union[str, Path, Dict[str, Any]]) -> "NoidPlayer":
        """
        Load a scene from a file path, a JSON string, or a plain dict.

        When given a file path, imports with relative '.py' paths are resolved
        relative to that file's parent directory.
        """
        if isinstance(scene, dict):
            self._scene_dir = None
            return self._load_data(scene)

        path = Path(scene)
        if path.exists():
            self._scene_dir = path.parent.resolve()
            return self._load_data(json.loads(path.read_text(encoding="utf-8")))

        # Treat as raw JSON string
        self._scene_dir = None
        return self._load_data(json.loads(scene))

    def _load_data(self, data: Dict[str, Any]) -> "NoidPlayer":
        self.title = data.get("title", "")

        for entry in data.get("imports", []):
            self._import(entry)

        for itf_spec in data.get("interfaces", []):
            Noid.c_interface(itf_spec)

        for spec in data.get("register", []):
            Noid.register(spec)

        for entry in data.get("components", []):
            comp = self._instantiate(entry)
            if comp is not None:
                self._components.append(comp)
                if entry.get("threaded", False):
                    comp.start_in_thread()

        return self

    def _import(self, path_or_module: str) -> None:
        if path_or_module.endswith(".py"):
            fpath = Path(path_or_module)
            if not fpath.is_absolute() and self._scene_dir:
                fpath = (self._scene_dir / fpath).resolve()
            spec = importlib.util.spec_from_file_location(fpath.stem, fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        else:
            importlib.import_module(path_or_module)

    def _instantiate(self, entry: Dict[str, Any]) -> Optional[OidComponent]:
        comp_type = entry.get("type")
        if comp_type is None:
            return None
        return Noid.create(
            comp_type,
            properties=entry.get("properties"),
            bus=self._bus,
            component_instance_id=entry.get("id"),
            subscribe=entry.get("subscribe"),
            publish=entry.get("publish"),
            connect=entry.get("connect"),
        )

    # ---------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Start all non-threaded components in the current event loop."""
        for comp in self._components:
            if not comp._threaded:
                await comp.start()

    async def stop(self) -> None:
        """Stop all components (signal threaded ones and join them)."""
        for comp in self._components:
            if comp._threaded:
                comp.stop_thread()
            else:
                await comp.stop()
        for comp in self._components:
            if comp._threaded:
                comp.join_thread(timeout=5.0)
        self._components.clear()

    async def run(self, *, timeout: Optional[float] = None) -> None:
        """
        Start all non-threaded components and block until:
        - the 'player/done' topic is published on the bus,
        - *timeout* seconds elapse (if given), or
        - a KeyboardInterrupt / asyncio.CancelledError is raised.

        Calls stop() unconditionally in the finally block.
        """
        done = asyncio.Event()
        _done_handler = lambda _t, _m: done.set()
        self._bus.subscribe("player/done", _done_handler)
        await self.start()
        await self._bus.publish("player/start", {})
        try:
            if timeout is not None:
                try:
                    await asyncio.wait_for(done.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            else:
                await done.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._bus.unsubscribe("player/done", _done_handler)
            await self.stop()

    # ------------------------------------------------------- convenience entry

    @classmethod
    def play(
        cls,
        scene: Union[str, Path, Dict[str, Any]],
        *,
        timeout: Optional[float] = None,
        bus: Optional[Bus] = None,
    ) -> None:
        """Load and run a scene synchronously (wraps asyncio.run)."""
        player = cls(bus=bus)
        player.load(scene)
        asyncio.run(player.run(timeout=timeout))


def _cli(argv=None) -> None:
    """
    Command-line entry point.

    Usage:
        noid-play scene.json
        noid-play scene.json --timeout 30
        python -m noid scene.json
        python -m noid.core.player scene.json
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="noid-play",
        description="Run a noid JSON scene file.",
    )
    parser.add_argument(
        "scene",
        help="Path to the JSON scene file to load and run.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop after this many seconds even if player/done is not published.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help=(
            "Print bus traffic to the console: each published message shows "
            "its topic, the publishing component, the receiving components, "
            "and the message payload."
        ),
    )
    args = parser.parse_args(argv)

    bus = Bus()
    monitor = None
    if args.verbose:
        from noid.management.bus_monitor import BusMonitor
        monitor = BusMonitor(bus)
        monitor.start()
    try:
        NoidPlayer.play(args.scene, timeout=args.timeout, bus=bus)
    finally:
        if monitor:
            monitor.stop()


if __name__ == "__main__":
    _cli()
