import dataclasses

import pytest

from kibana_mcp.core.errors import KibanaMcpError, KibanaNotFound, UnsafeDashboardError
from kibana_mcp.core.models import DataViewDetail, DashboardDetail, PanelSummary


def test_errors_carry_message():
    err = KibanaNotFound("data view 'flighs' not found")
    assert isinstance(err, KibanaMcpError)
    assert "flighs" in str(err)
    assert issubclass(UnsafeDashboardError, KibanaMcpError)


def test_dtos_are_frozen():
    dv = DataViewDetail(
        id="abc", name="flights", index_pattern="kibana_sample_data_flights",
        time_field="timestamp", fields={"Carrier": "string"},
    )
    d = DashboardDetail(
        id="d1", title="t", description="",
        panels=(PanelSummary(index=0, type="vis", title="chart"),),
    )
    assert dv.fields["Carrier"] == "string"
    assert d.panels[0].index == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        dv.name = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.title = "changed"  # type: ignore[misc]


def test_stream_write_result_is_frozen():
    from kibana_mcp.core.models import StreamWriteResult
    r = StreamWriteResult(acknowledged=True, result="created")
    assert (r.acknowledged, r.result) == (True, "created")
    import dataclasses

    import pytest as _pytest
    with _pytest.raises(dataclasses.FrozenInstanceError):
        r.result = "x"  # type: ignore[misc]
