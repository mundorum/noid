"""
OidBase — Python port of JS OidBase + Primitive.

The JS hierarchy (Primitive → OidBase) is collapsed into a single class
because there is no HTMLElement to extend in the Python side.
"""
import asyncio
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from noid.core.bus import Bus, _current_publisher


class _OwnedHandler:
    """
    Thin callable wrapper that carries the owning component's id so that
    Bus.publish can report named receivers to monitor handlers without
    storing any component references inside the Bus itself.
    """

    __slots__ = ("_fn", "noid_owner")

    def __init__(self, fn: Callable, owner: str) -> None:
        self._fn = fn
        self.noid_owner = owner

    def __call__(self, topic: str, message: Any) -> Any:
        return self._fn(topic, message)


class OidBase:
    """
    Base class for all noid components.

    Mirrors the JS OidBase API (oid-base.js) with Python naming conventions.
    The 'notice → publish → subscribe → notice' data-flow pattern is preserved:
    - Components declare receive and publish mappings in their spec.
    - Incoming bus messages are routed to handle_* methods via handle_notice().
    - Outgoing events are published via _notify(notice, message).

    Threading model
    ---------------
    Two modes are supported:

    Shared-loop (default) — all components run in the same asyncio event loop:
        comp = MyOid(bus=Bus.i, properties={"name": "World"})
        await comp.start()
        ...
        await comp.stop()

    Dedicated thread — each component gets its own thread + event loop:
        comp = MyOid(bus=Bus.i)
        comp.start_in_thread()   # returns immediately
        ...
        comp.stop_thread()       # signal stop
        comp.join_thread()       # wait for clean exit

    In threaded mode every bus handler is wrapped with a cross-thread
    dispatcher that safely forwards messages to the component's own event loop
    using asyncio.run_coroutine_threadsafe / loop.call_soon_threadsafe.
    """

    def __init__(
        self,
        *,
        bus: Optional[Bus] = None,
        component_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        subscribe: Optional[Union[str, Dict[str, str]]] = None,
        publish: Optional[Union[str, Dict[str, str]]] = None,
        connect: Optional[Union[str, List[str]]] = None,
    ) -> None:
        self._bus: Bus = bus if bus is not None else Bus.i

        # Stable bound-method references — must be the same object on every
        # access so that bus.unsubscribe() can find them by identity.
        self._convert_notice_ref: Callable = self._convert_notice
        self._handle_notice_ref: Callable = self.handle_notice

        # topic → notice name  (for _convert_notice dispatch)
        self._map_topic_notice: Dict[str, str] = {}
        # wildcard patterns  (pattern, notice, original-filter)
        self._rgx_topic_notice: List[Tuple[re.Pattern, str, str]] = []
        # notice → topic  (for _notify)
        self._map_notice_topic: Dict[str, str] = {}

        # Handler dispatch tables built from spec
        self._receive_handler: Dict[str, Callable] = {}
        self._provide_handler: Dict[str, Callable] = {}

        # Connection tracking: interface_id → [component_id, ...]
        self._connected: Dict[str, List[str]] = {}

        # Tracks every (topic, original_handler, actual_bus_handler) triple
        # so _finalize can unsubscribe the right function objects even in
        # threaded mode where actual_bus_handler is a wrapped dispatcher.
        self._subscriptions: List[Tuple[str, Callable, Callable]] = []

        # Readiness queue — messages received while not ready are buffered here
        self._ready: bool = True
        self._pending_messages: List[Tuple[str, Any]] = []

        # Threading state
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._threaded: bool = False

        # Deferred wiring — applied in start() / _initialize()
        self._init_subscribe = subscribe
        self._init_publish = publish
        self._init_connect = connect

        self._component_id: Optional[str] = component_id

        # Apply construction-time properties before _initialize so that spec
        # defaults don't accidentally overwrite explicit caller values.
        if properties:
            for k, v in properties.items():
                setattr(self, k, v)

    # ------------------------------------------------------------------
    # Lifecycle  (mirrors JS connectedCallback / disconnectedCallback)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start in the caller's currently running event loop (shared-loop mode)."""
        self._loop = asyncio.get_running_loop()
        await self._initialize()

    async def stop(self) -> None:
        """Unsubscribe and withdraw all provided interfaces."""
        self._finalize()

    def start_in_thread(self) -> None:
        """
        Start the component in a dedicated daemon thread with its own asyncio
        event loop.  Blocks until the thread is up AND _initialize() has
        completed (subscriptions and providers are live before this returns).
        """
        self._threaded = True
        _ready = threading.Event()

        def _thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _boot():
                self._stop_event = asyncio.Event()
                await self.start()   # wire up + any subclass overrides
                _ready.set()
                await self._stop_event.wait()
                await self.stop()    # full cleanup + any subclass overrides

            loop.run_until_complete(_boot())
            loop.close()

        self._thread = threading.Thread(target=_thread_main, daemon=True)
        self._thread.start()
        _ready.wait()  # block until subscriptions are live

    def stop_thread(self) -> None:
        """Signal the threaded component to stop (non-blocking)."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def join_thread(self, timeout: Optional[float] = None) -> None:
        """Block until the component's thread has finished."""
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    # Initialization / finalization  (mirrors JS _initialize / _finalize)
    # ------------------------------------------------------------------

    async def _initialize(self) -> None:
        spec = getattr(type(self), "_spec", None)
        if spec:
            self._build_receive_handlers(spec.get("receive"))
            self._build_providers()
            self._build_provide_handlers()

            # Apply spec property defaults only when not already set at ctor time
            for pname, pdef in (spec.get("properties") or {}).items():
                default = pdef.get("default")
                private = "_prop_" + pname
                if default is not None and not hasattr(self, private):
                    setattr(self, private, default)

            if spec.get("subscribe"):
                self._subscribe_topic_notice(spec["subscribe"])
            if spec.get("publish"):
                self._publish_notice_topic(spec["publish"])
            if spec.get("connect"):
                self._connect_interface(spec["connect"])

        # Apply instance-level overrides (constructor args or Noid.create kwargs)
        if self._init_subscribe:
            self._subscribe_topic_notice(self._init_subscribe)
        if self._init_publish:
            self._publish_notice_topic(self._init_publish)
        if self._init_connect:
            self._connect_interface(self._init_connect)

    def _finalize(self) -> None:
        """Unsubscribe all topics and withdraw all provided interfaces."""
        for _topic, _orig, actual in list(self._subscriptions):
            self._bus.unsubscribe(_topic, actual)
        self._subscriptions.clear()
        self._remove_providers()

    # ------------------------------------------------------------------
    # Handler table building  (mirrors JS _buildHandlers / _buildProviders)
    # ------------------------------------------------------------------

    def _build_receive_handlers(self, receive_spec) -> None:
        self._build_handlers(self._receive_handler, receive_spec, c_interface=None)

    def _build_providers(self) -> None:
        spec = getattr(type(self), "_spec", None)
        if spec and spec.get("provide") and self._component_id:
            for c_interface in spec["provide"]:
                self._provide(c_interface, self._component_id, self)

    def _remove_providers(self) -> None:
        spec = getattr(type(self), "_spec", None)
        if spec and spec.get("provide") and self._component_id:
            for c_interface in spec["provide"]:
                self._withhold(c_interface, self._component_id)

    def _build_provide_handlers(self) -> None:
        spec = getattr(type(self), "_spec", None)
        if not (spec and spec.get("provide")):
            return
        # Late import to break the circular reference: base → component → base
        from noid.core.component import Noid
        for c_interface in spec["provide"]:
            itf_spec = Noid.get_interface(c_interface)
            if itf_spec and itf_spec.get("operations"):
                self._build_handlers(
                    self._provide_handler,
                    itf_spec["operations"],
                    c_interface=c_interface,
                )

    def _build_handlers(
        self,
        handler_set: Dict[str, Callable],
        handlers_spec,
        c_interface: Optional[str],
    ) -> None:
        """
        Populate handler_set from a spec.receive or interface.operations block.

        Array form:  ['test', 'update']
            → handle_test, handle_update
        Dict form:   {'test': 'my_handler'} or {'test': {'handler': 'my_handler'}}
            → self.my_handler
        """
        if not handlers_spec:
            return
        prefix = (c_interface + ".") if c_interface else ""

        if isinstance(handlers_spec, list):
            for notice in handlers_spec:
                key = prefix + notice
                if key not in handler_set:
                    method = getattr(self, _notice_to_method(notice), None)
                    if method is not None:
                        handler_set[key] = method
        elif isinstance(handlers_spec, dict):
            for notice, notice_spec in handlers_spec.items():
                key = prefix + notice
                if key not in handler_set:
                    if isinstance(notice_spec, str):
                        method_name = notice_spec
                    elif isinstance(notice_spec, dict) and notice_spec.get("handler"):
                        method_name = notice_spec["handler"]
                    else:
                        method_name = _notice_to_method(notice)
                    method = getattr(self, method_name, None)
                    if method is not None:
                        handler_set[key] = method

    # ------------------------------------------------------------------
    # Bus proxy methods  (mirrors JS Primitive._subscribe etc.)
    # ------------------------------------------------------------------

    def _subscribe(self, topic_or_dict, handler: Optional[Callable] = None) -> None:
        """Subscribe to the bus, wrapping handler for thread-safe dispatch if needed."""
        if self._threaded and handler is not None:
            actual = self._make_thread_dispatcher(handler)
        else:
            actual = handler
        # Tag with an owner label so Bus.publish can name this receiver in monitor output.
        # Use the instance id when set, otherwise fall back to the spec type id so that
        # anonymous component instances (no "id" in the scene) still appear by type.
        owner = self._component_id or (getattr(type(self), "_spec", None) or {}).get("id")
        if actual is not None and owner:
            actual = _OwnedHandler(actual, owner)
        # Track for later unsubscribe
        if handler is not None:
            self._subscriptions.append((topic_or_dict, handler, actual))
        self._bus.subscribe(topic_or_dict, actual)

    def _unsubscribe(self, topic: str, handler: Callable) -> None:
        """Unsubscribe by original handler reference (works transparently in threaded mode)."""
        for i, (t, h, actual) in enumerate(self._subscriptions):
            if t == topic and h is handler:
                self._bus.unsubscribe(topic, actual)
                self._subscriptions.pop(i)
                return

    async def _publish(self, topic: str, message: Any) -> None:
        owner = self._component_id or (getattr(type(self), "_spec", None) or {}).get("id")
        token = _current_publisher.set(owner)
        try:
            await self._bus.publish(topic, message)
        finally:
            _current_publisher.reset(token)

    def _provide(self, c_interface: str, component_id: str, provider: Any) -> bool:
        return self._bus.provide(c_interface, component_id, provider)

    def _withhold(self, c_interface: str, component_id: str) -> bool:
        return self._bus.withhold(c_interface, component_id)

    def _connect(self, c_interface: str, component_id: str, callback: Any) -> bool:
        return self._bus.connect(c_interface, component_id, callback)

    async def _invoke(self, c_interface: str, notice: str, message: Any) -> Any:
        """
        Invoke an operation on all connected providers.
        If the interface spec declares response: True, returns a list of all
        responses; otherwise returns the response from the first provider.
        """
        if not self._connected.get(c_interface):
            return None
        from noid.core.component import Noid
        itf_spec = Noid.get_interface(c_interface) or {}
        multi = itf_spec.get("response") is True
        if multi:
            responses = []
            for cid in self._connected[c_interface]:
                responses.append(
                    await self._bus.invoke(c_interface, cid, notice, message)
                )
            return responses
        return await self._bus.invoke(
            c_interface, self._connected[c_interface][0], notice, message
        )

    # ------------------------------------------------------------------
    # Thread-safe handler dispatch
    # ------------------------------------------------------------------

    def _make_thread_dispatcher(self, handler: Callable) -> Callable:
        """
        Return a wrapper around handler that, when called from any thread,
        safely delivers the call into this component's own event loop.

        - Same loop  → call handler; if it returns a coroutine, create_task it
        - Other thread → schedule a closure in my_loop via call_soon_threadsafe;
                         that closure calls handler and, if needed, create_tasks
                         any returned coroutine

        Returning None to the caller (bus) prevents the bus from trying to await
        the coroutine in the wrong event loop.
        """
        my_loop = self._loop

        def dispatcher(topic: str, message: Any) -> None:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None

            if current is my_loop:
                result = handler(topic, message)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            else:
                def _call_in_loop() -> None:
                    result = handler(topic, message)
                    if asyncio.iscoroutine(result):
                        my_loop.create_task(result)

                my_loop.call_soon_threadsafe(_call_in_loop)

        return dispatcher

    # ------------------------------------------------------------------
    # Notice handling  (mirrors JS handleNotice / handleInvoke / _notify)
    # ------------------------------------------------------------------

    def handle_notice(self, notice: str, message: Any) -> Any:
        """
        Dispatch an incoming notice to the matching receive handler.

        If the component is not ready (set_ready(False) was called), the notice
        is queued and will be replayed in order once set_ready(True) is called.

        Returns the handler's return value so that coroutines produced by async
        handlers bubble up through _convert_notice to the bus, which then awaits
        them in the correct event loop.
        """
        if not self._ready:
            self._pending_messages.append((notice, message))
            return None
        return self._dispatch_notice(notice, message)

    def _dispatch_notice(self, notice: str, message: Any) -> Any:
        """
        Core dispatch: route notice to its handler or auto-relay via publish map.

        Only the first path segment is used as the dispatch key
        (e.g. 'update/partial' dispatches to handle_update).

        Auto-relay (Python extension): if no handler is registered for this notice
        but a publish mapping exists, the message is forwarded automatically.  This
        enables pure-JSON relay components defined via Noid.register() or the
        player's "register" section without requiring a custom Python class.
        """
        notice_main = notice.split("/")[0] if notice and "/" in notice else notice
        handler = self._receive_handler.get(notice_main)
        if handler is not None:
            return handler(notice, message)
        # Auto-relay: return the _notify coroutine so the bus awaits it.
        if notice_main in self._map_notice_topic:
            return self._notify(notice_main, message)
        return None

    def set_ready(self, ready: bool) -> None:
        """
        Mark this component as ready or not ready to process incoming notices.

        While not ready, incoming notices are buffered in arrival order.
        Calling set_ready(True) schedules a drain: each buffered notice is
        dispatched in FIFO order through the normal handle_notice path.

        Typical use — prevent concurrent processing of an async handler::

            async def handle_input(self, notice, message):
                self.set_ready(False)
                try:
                    result = await some_slow_call(message)
                    await self._notify("output", result)
                finally:
                    self.set_ready(True)
        """
        was_ready = self._ready
        self._ready = ready
        if ready and not was_ready and self._pending_messages:
            self._schedule_drain()

    def _schedule_drain(self) -> None:
        """Schedule _drain_pending() in the component's event loop."""
        if self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
            if running is self._loop:
                asyncio.create_task(self._drain_pending())
                return
        except RuntimeError:
            pass
        self._loop.call_soon_threadsafe(
            lambda: self._loop.create_task(self._drain_pending())
        )

    async def _drain_pending(self) -> None:
        """Replay buffered notices in FIFO order, stopping if set_ready(False) is called."""
        while self._pending_messages and self._ready:
            notice, message = self._pending_messages.pop(0)
            result = self._dispatch_notice(notice, message)
            if asyncio.iscoroutine(result):
                await result

    async def handle_invoke(self, c_interface: str, notice: str, message: Any) -> Any:
        """Dispatch an invoke call to the matching provide handler."""
        key = f"{c_interface}.{notice}"
        handler = self._provide_handler.get(key)
        if handler is None:
            return None
        result = handler(notice, message)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _notify(self, notice: str, message: Any) -> None:
        """
        Trigger a notice: if a publish mapping exists for it, publishes on the bus.
        Mirrors JS this._notify(notice, message).
        """
        topic = self._map_notice_topic.get(notice)
        if topic is not None:
            await self._publish(topic, message)

    def _convert_notice(self, topic: str, message: Any) -> Any:
        """
        Adapter subscribed to wildcard or mapped topics.  Translates the
        arriving topic into its mapped notice name and dispatches to handle_notice.

        Returns handle_notice's return value so that coroutines from async handlers
        propagate back to the bus for awaiting.
        """
        notice = self._map_topic_notice.get(topic)
        if notice is not None:
            return self.handle_notice(notice, message)
        for pattern, notice, _ in self._rgx_topic_notice:
            if pattern.fullmatch(topic):
                return self.handle_notice(notice, message)
        return None

    def connection_ready(
        self, c_interface: str, component_id: str, provider: Any
    ) -> None:
        """Called by the bus when a connection request can be fulfilled."""
        self._connected.setdefault(c_interface, []).append(component_id)

    def connect_to(self, c_interface: str, component: "OidBase") -> None:
        """Convenience: connect to another component by reference (no id lookup needed)."""
        if component.component_id:
            self._connect(c_interface, component.component_id, self)

    # ------------------------------------------------------------------
    # Default built-in handlers  (mirrors JS handleGet / handleSet)
    # ------------------------------------------------------------------

    def handle_get(self, notice: str, message: Any) -> Any:
        prop = message.get("property") if isinstance(message, dict) else None
        return getattr(self, prop, None) if prop else None

    def handle_set(self, notice: str, message: Any) -> None:
        if isinstance(message, dict):
            prop, value = message.get("property"), message.get("value")
            if prop is not None and value is not None:
                setattr(self, prop, value)

    # ------------------------------------------------------------------
    # Spec-string parsers  (mirrors JS _subscribeTopicNotice etc.)
    # ------------------------------------------------------------------

    def _subscribe_topic_notice(
        self, topic_notice: Union[str, Dict[str, str]]
    ) -> None:
        """
        Wire up bus subscriptions from a subscribe mapping.

        String syntax: "topic~notice;topic2~notice2"
            topic is subscribed; on arrival, notice is dispatched to handle_notice.
        Dict syntax:   {"topic": "notice", "topic2": "notice2"}

        If no '~' separator, the topic itself is used as the notice name.
        """
        pairs = (
            topic_notice.items()
            if isinstance(topic_notice, dict)
            else _parse_tilde_pairs(topic_notice)
        )
        for topic, notice in pairs:
            if notice and notice != topic:
                if "+" in topic or "#" in topic:
                    self._rgx_topic_notice.append(
                        (Bus._convert_regexp(topic), notice, topic)
                    )
                else:
                    self._map_topic_notice[topic] = notice
                self._subscribe(topic, self._convert_notice_ref)
            else:
                self._map_topic_notice[topic] = topic
                self._subscribe(topic, self._handle_notice_ref)

    def _publish_notice_topic(
        self, notice_topic: Union[str, Dict[str, str]]
    ) -> None:
        """
        Register notice → topic publish mappings.

        String syntax: "notice~topic;notice2~topic2"
        Dict syntax:   {"notice": "topic"}
        """
        pairs = (
            notice_topic.items()
            if isinstance(notice_topic, dict)
            else _parse_tilde_pairs(notice_topic)
        )
        for notice, topic in pairs:
            self._map_notice_topic[notice] = topic

    def _connect_interface(
        self, id_interface: Union[str, List[str]]
    ) -> bool:
        """
        Request connections to one or more providers.

        String syntax: "itf:name#component_id;itf:other#other_id"
        List syntax:   ["itf:name#component_id", ...]
        """
        items = (
            [s.strip() for s in id_interface.split(";")]
            if isinstance(id_interface, str)
            else id_interface
        )
        ok = True
        for ii in items:
            parts = [p.strip() for p in ii.split("#")]
            if len(parts) == 2:
                self._connect(parts[0], parts[1], self)
            else:
                ok = False
        return ok

    # ------------------------------------------------------------------
    # component_id property  (mirrors JS id, renamed to avoid shadowing builtin)
    # ------------------------------------------------------------------

    @property
    def component_id(self) -> Optional[str]:
        return self._component_id

    @component_id.setter
    def component_id(self, new_id: Optional[str]) -> None:
        if self._component_id and self._loop:
            self._remove_providers()
        self._component_id = new_id
        if self._loop:
            self._build_providers()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _notice_to_method(notice: str) -> str:
    """
    Convert a notice name to its Python handler method name.

    Examples:
        'test'        → 'handle_test'
        'updateValue' → 'handle_update_value'   (camelCase → snake_case)
        'my_notice'   → 'handle_my_notice'
    """
    snake = re.sub(r"([A-Z])", r"_\1", notice).lower().lstrip("_")
    return "handle_" + snake


def _parse_tilde_pairs(spec: str) -> List[Tuple[str, str]]:
    """
    Parse "a~b;c~d" into [('a','b'), ('c','d')].
    Entries without '~' produce ('x','x') (topic == notice).
    """
    result = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        segments = part.split("~", maxsplit=1)
        if len(segments) == 2:
            result.append((segments[0].strip(), segments[1].strip()))
        else:
            result.append((part, part))
    return result
