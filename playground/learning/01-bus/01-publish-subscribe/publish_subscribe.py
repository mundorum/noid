"""
Bus publish/subscribe — basic example.

Mirrors: oid/playground/learning/02-development/01-bus/01-publish-subscribe/index.html
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.bus import Bus


def notify(topic: str, message: dict) -> None:
    print(f"[{topic}] {message['value']}")


async def main() -> None:
    # subscriber
    Bus.i.subscribe("test/topic", notify)

    # publisher
    await Bus.i.publish("test/topic", {"value": "Hello, world!"})


asyncio.run(main())
