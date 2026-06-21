"""
OidComponent and Noid — user-facing component class and registry.

Mirrors the JS Oid class (oid.js) and the OidUI/OidBase hierarchy.
Because noid has no rendering layer, OidComponent collapses OidBase and OidUI
into a single Python class: subclass OidComponent for all component work.
"""
import re
from typing import Any, Callable, Dict, List, Optional, Type, Union

from noid.core.base import OidBase
from noid.core.bus import Bus


class OidComponent(OidBase):
    """
    Primary extension point for application components.

    Subclass this and decorate with @Noid.component(spec) to register
    a reusable component with the noid registry.

    Example (decorator form)::

        @Noid.component({
            "id": "ex:greeter",
            "properties": {"name": {"default": "World"}},
            "receive": ["greet"],
            "publish": "done~greeter/done",
        })
        class GreeterOid(OidComponent):
            async def handle_greet(self, notice, message):
                print(f"Hello, {self.name}!")
                await self._notify("done", {"greeted": self.name})

        comp = Noid.create("ex:greeter", {"name": "Alice"})
        await comp.start()

    Example (JSON / declarative form — no custom class needed)::

        Noid.register({
            "id": "ex:logger",
            "subscribe": "sensor/#~log",
        })
        logger = Noid.create("ex:logger")
        await logger.start()
    """


class Noid:
    """
    Component registry and factory.  Python equivalent of the JS Oid class.

    Interface registration::

        Noid.c_interface({
            "id": "itf:transfer",
            "operations": {"send": {"response": False}},
        })

    Component registration (decorator)::

        @Noid.component({"id": "ex:hello", "receive": ["greet"]})
        class HelloOid(OidComponent):
            async def handle_greet(self, notice, message):
                ...

    Component registration (JSON / no custom class)::

        Noid.register({"id": "ex:sensor", "properties": {"value": {"default": 0}}})

    Instance creation (mirrors JS Oid.create)::

        comp = Noid.create("ex:hello", {"name": "World"})
        await comp.start()
    """

    _interface_reg: Dict[str, Dict] = {}
    _oid_reg: Dict[str, Type[OidComponent]] = {}

    # ------------------------------------------------------------------
    # Interface registry  (mirrors JS Oid.cInterface / Oid.getInterface)
    # ------------------------------------------------------------------

    @classmethod
    def c_interface(cls, spec: Dict) -> None:
        """Register an interface specification by id."""
        if spec and spec.get("id"):
            cls._interface_reg[spec["id"]] = spec

    @classmethod
    def get_interface(cls, c_interface: str) -> Optional[Dict]:
        """Return the registered spec for an interface, or None."""
        return cls._interface_reg.get(c_interface)

    # ------------------------------------------------------------------
    # Component registration  (mirrors JS Oid.component)
    # ------------------------------------------------------------------

    @classmethod
    def component(cls, spec: Dict) -> Callable:
        """
        Register a component class.  Use as a class decorator:

            @Noid.component({"id": "ex:hello", "receive": ["greet"]})
            class HelloOid(OidComponent):
                async def handle_greet(self, notice, message): ...

        Or with 'implementation' in the spec for the non-decorator form:

            Noid.component({"id": "ex:hello", "implementation": HelloOid, ...})

        Returns the class unchanged (after attaching _spec and property descriptors).
        """
        impl = spec.pop("implementation", None)

        def _register(klass: Type[OidComponent]) -> Type[OidComponent]:
            klass._spec = dict(spec)
            cls._add_property_descriptors(klass, spec.get("properties") or {})
            cls._oid_reg[spec["id"]] = klass
            return klass

        if impl is not None:
            return _register(impl)
        return _register  # decorator form: called with the class next

    @classmethod
    def register(cls, spec: Dict) -> Type[OidComponent]:
        """
        Register a component from a pure JSON/dict spec (no custom Python class).

        A generic OidComponent subclass is created automatically — the component
        behaviour is fully determined by its spec mappings (subscribe, publish,
        receive, provide, connect) and the built-in OidBase machinery.

            Noid.register({
                "id": "ex:sensor",
                "properties": {"value": {"default": 0}},
                "receive": ["update"],
                "publish": "update~sensor/reading",
            })
            sensor = Noid.create("ex:sensor", {"value": 42})
            await sensor.start()
        """
        class_name = _spec_id_to_class_name(spec["id"])
        klass: Type[OidComponent] = type(class_name, (OidComponent,), {})
        klass._spec = dict(spec)
        cls._add_property_descriptors(klass, spec.get("properties") or {})
        cls._oid_reg[spec["id"]] = klass
        return klass

    # ------------------------------------------------------------------
    # Instance factory  (mirrors JS Oid.create)
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        component_id: str,
        properties: Optional[Dict[str, Any]] = None,
        *,
        bus: Optional[Bus] = None,
        component_instance_id: Optional[str] = None,
        subscribe: Optional[Union[str, Dict[str, str]]] = None,
        publish: Optional[Union[str, Dict[str, str]]] = None,
        connect: Optional[Union[str, List[str]]] = None,
    ) -> OidComponent:
        """
        Create an instance of a registered component.

        Mirrors JS Oid.create(componentId, properties).

            comp = Noid.create("ex:hello", {"name": "World"})
            await comp.start()

        component_instance_id sets the component_id used for bus provide/connect
        (distinct from the type's spec id).
        """
        klass = cls._oid_reg.get(component_id)
        if klass is None:
            raise KeyError(f"Unknown component id: {component_id!r}")
        return klass(
            bus=bus,
            component_id=component_instance_id,
            properties=properties,
            subscribe=subscribe,
            publish=publish,
            connect=connect,
        )

    # ------------------------------------------------------------------
    # Property descriptor injection
    # ------------------------------------------------------------------

    @classmethod
    def _add_property_descriptors(cls, klass: Type, props: Dict) -> None:
        """
        Add Python property descriptors to the class for each entry in the
        spec's 'properties' block.

        Each descriptor is backed by a _prop_<name> instance attribute.
        Spec defaults are applied in OidBase._initialize, not here, so that
        multiple instances of the same class remain independent.
        """
        for name, pdef in props.items():
            private = "_prop_" + name
            default = pdef.get("default")
            readonly = pdef.get("readonly", False)
            _attach_property(klass, name, private, default, readonly)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _attach_property(
    klass: type,
    name: str,
    private: str,
    default: Any,
    readonly: bool,
) -> None:
    """Add a property descriptor to klass for a single spec property."""
    def getter(self: Any) -> Any:
        return getattr(self, private, default)

    if readonly:
        setattr(klass, name, property(getter))
    else:
        def setter(self: Any, value: Any) -> None:
            setattr(self, private, value)
        setattr(klass, name, property(getter, setter))


def _spec_id_to_class_name(spec_id: str) -> str:
    """
    Derive a Python class name from a spec id.

    'ex:hello-world' → 'ExHelloWorldOid'
    'sensor:pressure' → 'SensorPressureOid'
    """
    parts = re.split(r"[:\-_]+", spec_id)
    return "".join(p.capitalize() for p in parts) + "Oid"
