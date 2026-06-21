import asyncio
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

Handler = Callable[..., Any]
_TopicMap = Dict[str, List[Handler]]
_RegexEntry = Tuple[re.Pattern, Handler, str]


class Bus:
    """
    Async publish/subscribe message bus with MQTT-style wildcard topic filtering
    and connection-oriented service discovery.

    Mirrors the JS oid Bus class API (bus.js) as closely as Python allows:
      - subscribe / unsubscribe  →  message-oriented, topic-based
      - publish                  →  async; handlers may be sync or async
      - provide / withhold       →  register / remove a service provider
      - connect                  →  link a caller to a provider (deferred if not yet ready)
      - invoke                   →  call a registered provider's handle_invoke()

    Thread safety: subscribe / unsubscribe / provide / withhold / connect are safe
    to call from any thread.  publish and invoke are async and must be awaited
    inside an event loop; to schedule them from another thread use
    asyncio.run_coroutine_threadsafe(bus.publish(...), loop).

    Wildcard rules (MQTT-inspired):
      +   matches exactly one topic level (no slashes)
      #   matches one or more topic levels (any characters, including slashes)
    """

    i: "Bus"

    def __init__(self) -> None:
        self._listeners: _TopicMap = {}
        self._listeners_rgx: List[_RegexEntry] = []
        self._providers: Dict[str, Any] = {}
        self._pending_cnx: Dict[str, List[Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Message-oriented communication
    # ------------------------------------------------------------------

    def subscribe(
        self,
        subscribed: Union[str, Dict[str, Handler]],
        handler: Optional[Handler] = None,
    ) -> None:
        """
        Subscribe to one or more topics.

        Two calling forms (mirrors JS):
            bus.subscribe('topic', handler)
            bus.subscribe({'topic1': handler1, 'topic2': handler2})
        """
        if subscribed is None:
            return
        if isinstance(subscribed, str) and handler is not None:
            topics: Dict[str, Handler] = {subscribed: handler}
        elif isinstance(subscribed, dict):
            topics = subscribed
        else:
            return

        with self._lock:
            listeners_rgx = list(self._listeners_rgx)
            listeners: _TopicMap = dict(self._listeners)
            for topic, h in topics.items():
                if h is None:
                    continue
                if "+" in topic or "#" in topic:
                    listeners_rgx.append((Bus._convert_regexp(topic), h, topic))
                else:
                    existing = list(listeners.get(topic, []))
                    existing.append(h)
                    listeners[topic] = existing
            self._listeners_rgx = listeners_rgx
            self._listeners = listeners

    def unsubscribe(
        self,
        subscribed: Union[str, Dict[str, Handler]],
        handler: Optional[Handler] = None,
    ) -> None:
        """
        Unsubscribe a handler from one or more topics.

        Two calling forms (mirrors JS):
            bus.unsubscribe('topic', handler)
            bus.unsubscribe({'topic1': handler1, 'topic2': handler2})
        """
        if subscribed is None:
            return
        if isinstance(subscribed, str) and handler is not None:
            topics: Dict[str, Handler] = {subscribed: handler}
        elif isinstance(subscribed, dict):
            topics = subscribed
        else:
            return

        with self._lock:
            listeners_rgx = list(self._listeners_rgx)
            listeners: _TopicMap = dict(self._listeners)
            for topic, h in topics.items():
                if "+" in topic or "#" in topic:
                    for i, entry in enumerate(listeners_rgx):
                        if entry[1] is h and entry[2] == topic:
                            listeners_rgx.pop(i)
                            break
                elif topic in listeners:
                    handlers = list(listeners[topic])
                    for i, existing in enumerate(handlers):
                        if existing is h:
                            handlers.pop(i)
                            listeners[topic] = handlers
                            break
            self._listeners_rgx = listeners_rgx
            self._listeners = listeners

    async def publish(self, topic: str, message: Any) -> None:
        """
        Publish a message to all subscribers whose topic pattern matches.
        Handlers may be plain callables or async coroutine functions.
        """
        with self._lock:
            exact = list(self._listeners.get(topic, []))
            rgx = list(self._listeners_rgx)

        for h in exact:
            result = h(topic, message)
            if asyncio.iscoroutine(result):
                await result

        for pattern, h, _ in rgx:
            if pattern.fullmatch(topic):
                result = h(topic, message)
                if asyncio.iscoroutine(result):
                    await result

    # ------------------------------------------------------------------
    # Message analysis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_regexp(filter_str: str) -> re.Pattern:
        """
        Convert an MQTT-style topic filter to a compiled regex.
        Mirrors JS Bus._convertRegExp.
        """
        pattern = (
            filter_str
            .replace("/", r"\/")
            .replace("+", r"[^\/]+")
            .replace("#", r".+")
        )
        return re.compile(pattern)

    # ------------------------------------------------------------------
    # Connection-oriented communication
    # ------------------------------------------------------------------

    def provide(self, c_interface: str, component_id: str, provider: Any) -> bool:
        """
        Register a service provider.  Returns False if already registered.
        Notifies any callers that connected before the provider was ready.

        Provider must implement: handle_invoke(c_interface, notice, message) -> Any
        (called by invoke; may be a coroutine function).
        """
        if component_id is None or c_interface is None or provider is None:
            return False
        key = f"{c_interface}#{component_id}"
        pending: List[Any] = []
        with self._lock:
            if key in self._providers:
                return False
            self._providers[key] = provider
            pending = self._pending_cnx.pop(key, [])
        for callback in pending:
            callback.connection_ready(c_interface, component_id, provider)
        return True

    def withhold(self, c_interface: str, component_id: str) -> bool:
        """Remove a previously registered service provider."""
        if component_id is None or c_interface is None:
            return False
        key = f"{c_interface}#{component_id}"
        with self._lock:
            if key not in self._providers:
                return False
            del self._providers[key]
        return True

    def connect(self, c_interface: str, component_id: str, callback: Any) -> bool:
        """
        Connect to a service provider.  If the provider is already registered,
        callback.connection_ready() is called immediately; otherwise it is queued
        until provide() is called.

        Callback must implement: connection_ready(c_interface, component_id, provider)
        """
        if component_id is None or c_interface is None or callback is None:
            return False
        key = f"{c_interface}#{component_id}"
        provider = None
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                self._pending_cnx.setdefault(key, []).append(callback)
        if provider is not None:
            callback.connection_ready(c_interface, component_id, provider)
        return True

    async def invoke(
        self,
        c_interface: str,
        component_id: str,
        notice: str,
        message: Any,
    ) -> Any:
        """
        Call handle_invoke on a registered provider.
        Returns None if no provider is registered for the given interface/id.
        """
        key = f"{c_interface}#{component_id}"
        with self._lock:
            provider = self._providers.get(key)
        if provider is None:
            return None
        result = provider.handle_invoke(c_interface, notice, message)
        if asyncio.iscoroutine(result):
            return await result
        return result


Bus.i = Bus()
