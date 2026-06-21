"""
02-publish-subscribe — two publishers, two subscribers with different patterns.

Mirrors JS playground/learning/01-page/02-publish-subscribe.

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
