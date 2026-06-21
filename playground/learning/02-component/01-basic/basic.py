"""
Basic component — declarative JSON spec, no custom class.

Shows the minimum needed to define a component from a spec dict (the Python
equivalent of declarative HTML <oid> elements), create an instance, and start it.

Analogous to the JS:
    Oid.component({ id: 'ex:basic', element: 'basic-oid', properties: { name: {} } })
    const c = Oid.create('ex:basic', { name: 'World' })
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.bus import Bus
from noid.core.component import Noid


# --- declarative spec registration (mirrors JSON / HTML approach) ---

Noid.register({
    "id": "ex:greeter",
    "properties": {
        "name": {"default": "World"},
    },
})


# --- programmatic instantiation ---

async def main() -> None:
    bus = Bus()

    comp = Noid.create("ex:greeter", {"name": "Alice"}, bus=bus)
    await comp.start()

    print(f"Component type : {type(comp).__name__}")
    print(f"Component id   : {comp.component_id}")
    print(f"name property  : {comp.name}")

    await comp.stop()


asyncio.run(main())
