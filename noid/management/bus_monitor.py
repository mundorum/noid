"""
BusMonitor — console traffic logger for the noid Bus.

Subscribes to the bus via subscribe_monitor() so that its own activity is
invisible to itself: it never appears as a subscriber in the receiver list
and never generates recursive monitor events.

Typical use::

    from noid.core.bus import Bus
    from noid.management.bus_monitor import BusMonitor

    bus = Bus()
    monitor = BusMonitor(bus)
    monitor.start()
    ...                 # run your scene
    monitor.stop()

Output format (one line per published message)::

    [bus] 'timer/tick' (from 'timer1', to 'counter1', 'display1'): {'count': 3}
    [bus] 'player/done' (from 'timer1', to (none)): None
"""
import sys
from typing import Any, List, Optional

from noid.core.bus import Bus


class BusMonitor:
    """Prints all bus traffic to the console without appearing in the traffic itself."""

    def __init__(self, bus: Bus, *, file=None) -> None:
        self._bus = bus
        self._file = file or sys.stdout
        self._handler = self._on_traffic

    def start(self) -> None:
        """Register with the bus — starts receiving all published messages."""
        self._bus.subscribe_monitor(self._handler)

    def stop(self) -> None:
        """Deregister from the bus."""
        self._bus.unsubscribe_monitor(self._handler)

    def _on_traffic(
        self,
        topic: str,
        message: Any,
        source: Optional[str],
        receivers: List[str],
    ) -> None:
        from_part = f"from '{source}'" if source else "from <unknown>"
        if receivers:
            to_part = "to " + ", ".join(f"'{r}'" for r in receivers)
        else:
            to_part = "to (none)"
        print(
            f"[bus] {topic!r} ({from_part}, {to_part}): {message!r}",
            file=self._file,
        )
