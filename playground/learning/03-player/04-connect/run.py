"""
04-connect — connection-oriented wiring (provide / connect) declared in JSON.

Mirrors JS playground/learning/01-page/06-connect:
    <file-oid connect="itf:transfer#presenter">
    <console-oid id="presenter">

Python equivalent:
    ex:sender connects to ex:store via the itf:transfer interface.
    The sender invokes store.send() and then signals player/done.

Run:
    python run.py
"""
import pathlib
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.player import NoidPlayer

scene = pathlib.Path(__file__).parent / "scene.json"
NoidPlayer.play(scene)
