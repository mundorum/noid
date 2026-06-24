"""Tests for noid.core.meta — component metadata extractor."""
import pytest

from noid.core.component import Noid, OidComponent
from noid.core.meta import _id_to_name, extract_meta


# ---------------------------------------------------------------------------
# Fixture: clean up test components after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registry():
    before = set(Noid._oid_reg.keys())
    yield
    for k in list(Noid._oid_reg.keys()):
        if k not in before:
            del Noid._oid_reg[k]


# ---------------------------------------------------------------------------
# id and name
# ---------------------------------------------------------------------------

def test_id_extracted():
    @Noid.component({"id": "test:comp-a"})
    class CompAOid(OidComponent):
        pass
    assert extract_meta("test:comp-a")["id"] == "test:comp-a"


def test_name_derived_from_id():
    @Noid.component({"id": "test:my-sensor"})
    class MySensorOid(OidComponent):
        pass
    assert extract_meta("test:my-sensor")["name"] == "My Sensor"


def test_name_explicit_in_spec():
    @Noid.component({"id": "test:comp-b", "name": "Custom Display Name"})
    class CompBOid(OidComponent):
        pass
    assert extract_meta("test:comp-b")["name"] == "Custom Display Name"


# ---------------------------------------------------------------------------
# description
# ---------------------------------------------------------------------------

def test_description_from_spec():
    @Noid.component({"id": "test:comp-c", "description": "Does something useful."})
    class CompCOid(OidComponent):
        pass
    assert extract_meta("test:comp-c")["description"] == "Does something useful."


def test_description_from_class_docstring():
    @Noid.component({"id": "test:comp-d"})
    class CompDOid(OidComponent):
        """Reads temperature values from the bus."""
    assert extract_meta("test:comp-d")["description"] == "Reads temperature values from the bus."


def test_description_spec_takes_priority_over_docstring():
    @Noid.component({"id": "test:comp-e", "description": "Spec wins."})
    class CompEOid(OidComponent):
        """Class docstring loses."""
    assert extract_meta("test:comp-e")["description"] == "Spec wins."


def test_no_description_when_absent():
    @Noid.component({"id": "test:comp-f"})
    class CompFOid(OidComponent):
        pass
    assert "description" not in extract_meta("test:comp-f")


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

def test_property_with_default_is_not_required():
    @Noid.component({
        "id": "test:props-a",
        "properties": {"unit": {"default": "°C"}},
    })
    class PropsAOid(OidComponent):
        pass
    p = extract_meta("test:props-a")["properties"]["unit"]
    assert p["default"] == "°C"
    assert p["required"] is False


def test_property_without_default_is_required():
    @Noid.component({
        "id": "test:props-b",
        "properties": {"url": {}},
    })
    class PropsBOid(OidComponent):
        pass
    p = extract_meta("test:props-b")["properties"]["url"]
    assert p["required"] is True
    assert "default" not in p


def test_property_description():
    @Noid.component({
        "id": "test:props-c",
        "properties": {"unit": {"default": "°C", "description": "Measurement unit."}},
    })
    class PropsCOid(OidComponent):
        pass
    p = extract_meta("test:props-c")["properties"]["unit"]
    assert p["description"] == "Measurement unit."


def test_property_readonly_flag():
    @Noid.component({
        "id": "test:props-d",
        "properties": {"version": {"default": "1.0", "readonly": True}},
    })
    class PropsDOid(OidComponent):
        pass
    p = extract_meta("test:props-d")["properties"]["version"]
    assert p["readonly"] is True


def test_non_readonly_property_omits_readonly_key():
    @Noid.component({
        "id": "test:props-e",
        "properties": {"label": {"default": "x"}},
    })
    class PropsEOid(OidComponent):
        pass
    p = extract_meta("test:props-e")["properties"]["label"]
    assert "readonly" not in p


# ---------------------------------------------------------------------------
# input_notices
# ---------------------------------------------------------------------------

def test_input_notices_list_form():
    @Noid.component({
        "id": "test:notices-a",
        "receive": ["trigger", "reset"],
    })
    class NoticesAOid(OidComponent):
        pass
    notices = extract_meta("test:notices-a")["input_notices"]
    assert set(notices.keys()) == {"trigger", "reset"}


def test_input_notices_dict_form_with_description():
    @Noid.component({
        "id": "test:notices-b",
        "receive": {
            "trigger": {"description": "Starts the process."},
            "reset": {"description": "Resets internal state."},
        },
    })
    class NoticesBOid(OidComponent):
        pass
    notices = extract_meta("test:notices-b")["input_notices"]
    assert notices["trigger"]["description"] == "Starts the process."
    assert notices["reset"]["description"] == "Resets internal state."


def test_input_notices_dict_form_legacy_string_value():
    # Old dict form where value is a method-name string — description stays empty.
    @Noid.component({
        "id": "test:notices-c",
        "receive": {"trigger": "on_trigger"},
    })
    class NoticesCOid(OidComponent):
        pass
    notices = extract_meta("test:notices-c")["input_notices"]
    assert "trigger" in notices
    assert notices["trigger"] == {}


# ---------------------------------------------------------------------------
# output_notices
# ---------------------------------------------------------------------------

def test_output_notices_from_explicit_spec():
    @Noid.component({
        "id": "test:out-a",
        "publish": "result~out/result",
        "output_notices": {
            "result": {"description": "The computed result."},
        },
    })
    class OutAOid(OidComponent):
        pass
    notices = extract_meta("test:out-a")["output_notices"]
    assert notices["result"]["description"] == "The computed result."


def test_output_notices_derived_from_publish_mapping():
    @Noid.component({
        "id": "test:out-b",
        "publish": "text~out/text;done~out/done",
    })
    class OutBOid(OidComponent):
        pass
    notices = extract_meta("test:out-b")["output_notices"]
    assert set(notices.keys()) == {"text", "done"}


def test_output_notices_explicit_wins_over_publish():
    @Noid.component({
        "id": "test:out-c",
        "publish": "result~out/result",
        "output_notices": {
            "result": {"description": "Described result."},
        },
    })
    class OutCOid(OidComponent):
        pass
    notices = extract_meta("test:out-c")["output_notices"]
    assert notices["result"]["description"] == "Described result."


# ---------------------------------------------------------------------------
# provides / connects
# ---------------------------------------------------------------------------

def test_provides_lists_interface_ids():
    Noid.c_interface({
        "id": "itf:test-svc",
        "operations": {"run": {"description": "Run the service."}},
    })

    @Noid.component({"id": "test:provider-a", "provide": ["itf:test-svc"]})
    class ProviderAOid(OidComponent):
        pass

    meta = extract_meta("test:provider-a")
    assert meta["provides"][0]["id"] == "itf:test-svc"
    assert meta["provides"][0]["operations"]["run"]["description"] == "Run the service."


def test_connects_lists_interface_ids_without_instance():
    @Noid.component({"id": "test:consumer-a", "connect": "itf:store#db1;itf:log#logger1"})
    class ConsumerAOid(OidComponent):
        pass
    assert extract_meta("test:consumer-a")["connects"] == ["itf:store", "itf:log"]


# ---------------------------------------------------------------------------
# unknown component
# ---------------------------------------------------------------------------

def test_unknown_component_raises_key_error():
    with pytest.raises(KeyError, match="not found"):
        extract_meta("test:does-not-exist")


# ---------------------------------------------------------------------------
# _id_to_name helper
# ---------------------------------------------------------------------------

def test_id_to_name_hyphen():
    assert _id_to_name("data:text-source") == "Text Source"


def test_id_to_name_multi_segment():
    assert _id_to_name("lm:lm-agent") == "Lm Agent"


def test_id_to_name_no_namespace():
    assert _id_to_name("simple") == "Simple"


def test_id_to_name_underscore():
    assert _id_to_name("ex:hello_world") == "Hello World"
