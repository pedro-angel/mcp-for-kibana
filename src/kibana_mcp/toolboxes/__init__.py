"""Registry of all toolboxes. Adding a toolbox = one entry here."""

from kibana_mcp.toolboxes.alerting.toolbox import AlertingToolbox
from kibana_mcp.toolboxes.base import Toolbox
from kibana_mcp.toolboxes.cases.toolbox import CasesToolbox
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from kibana_mcp.toolboxes.data_management.toolbox import DataManagementToolbox
from kibana_mcp.toolboxes.fleet.toolbox import FleetToolbox
from kibana_mcp.toolboxes.observability.toolbox import ObservabilityToolbox
from kibana_mcp.toolboxes.platform_admin.toolbox import PlatformAdminToolbox
from kibana_mcp.toolboxes.platform_health.toolbox import PlatformHealthToolbox
from kibana_mcp.toolboxes.security_detections.toolbox import SecurityDetectionsToolbox
from kibana_mcp.toolboxes.streams.toolbox import StreamsToolbox

TOOLBOXES: dict[str, Toolbox] = {
    "dashboards": DashboardsToolbox(),
    "data-management": DataManagementToolbox(),
    "alerting": AlertingToolbox(),
    "cases": CasesToolbox(),
    "platform-health": PlatformHealthToolbox(),
    "observability": ObservabilityToolbox(),
    "security-detections": SecurityDetectionsToolbox(),
    "platform-admin": PlatformAdminToolbox(),
    "streams": StreamsToolbox(),
    "fleet": FleetToolbox(),
}
