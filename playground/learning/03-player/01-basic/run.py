"""
01-basic — minimal player example.

Mirrors JS playground/learning/01-page/01-basic but for a server-side scene:
the JSON file is the 'HTML page' and NoidPlayer is the 'browser'.

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
