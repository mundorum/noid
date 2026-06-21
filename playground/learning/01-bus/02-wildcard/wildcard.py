"""
Bus wildcard subscriptions — demonstrates MQTT-style + and # filters.

Mirrors: oid/playground/learning/02-development/01-bus/02-wildcard/index.html

Expected output (order within same-publish may vary across wildcards):
  equal    (news/disease):   [news/disease]     dengue symptoms
  wildcard (news/#):         [news/disease]     dengue symptoms
  wildcard (news/#):         [news/drug]        coronavirus vaccine
  wildcard (news/#):         [news/dinosaur]    worldwide dinosaurs
  wildcard (+/dinosaur):     [news/dinosaur]    worldwide dinosaurs
  wildcard (news/#):         [news/dinosaur/brazil]  brazilian dinosaurs
  wildcard (+/dinosaur):     [report/dinosaur]  dinosaurs survey
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.bus import Bus


def notify_equal(topic: str, message: dict) -> None:
    print(f"  equal    (news/disease):  [{topic}] {message['value']}")


def notify_wildcard_one(topic: str, message: dict) -> None:
    print(f"  wildcard (+/dinosaur):    [{topic}] {message['value']}")


def notify_wildcard_several(topic: str, message: dict) -> None:
    print(f"  wildcard (news/#):        [{topic}] {message['value']}")


async def main() -> None:
    bus = Bus()

    # subscribers
    bus.subscribe("news/disease", notify_equal)
    bus.subscribe("+/dinosaur", notify_wildcard_one)
    bus.subscribe("news/#", notify_wildcard_several)

    # publishers
    await bus.publish("news/disease", {"value": "dengue symptoms"})
    await bus.publish("news/drug", {"value": "coronavirus vaccine"})
    await bus.publish("news/dinosaur", {"value": "worldwide dinosaurs"})
    await bus.publish("news/dinosaur/brazil", {"value": "brazilian dinosaurs"})
    await bus.publish("report/dinosaur", {"value": "dinosaurs survey"})


asyncio.run(main())
