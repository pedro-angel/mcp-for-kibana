"""The streams toolbox (Wave 4): tiered access to Kibana Streams — the
logs/observability data streams and their ingest configuration.

The Kibana Streams API is **Tech-Preview**: its shapes may change between minor
versions. `read` tools list/get streams; the `write` tier enables/resyncs the
framework, forks a staged (disabled) child, edits a stream's processing steps,
and deactivates a forked child's routing; the `destructive` tier sets retention
(can age out data), activates a forked child (diverts live documents into it),
deletes a stream (destroys its backing data + any subtree), and disables the
framework (deletes ALL wired streams + data cluster-wide). Every write op
affects live data ingest — hence the tier gating. The query / significant-events
/ attachments surface needs an **Enterprise** license (403 on Basic) and stays
deferred (#58).
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.toolboxes.base import ToolboxDeps, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_WRITE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


class StreamsToolbox:
    name = "streams"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_streams() -> list[dict[str, Any]]:
            """List all Kibana Streams: each stream's name, type ('wired' =
            managed schema/routing, 'classic' = an existing data stream), and
            description. (Kibana Streams is a Tech-Preview feature.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(s) for s in gw.list_streams()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_stream(name: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one stream's summary by name (e.g. 'logs.ecs'): type,
            description, last-updated, its data lifecycle mode + retention, and
            counts of processing steps, child-routing rules, and managed fields.
            Use get_stream_ingest for the actual field schema. Errors if the
            stream does not exist."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_stream(name))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_stream_ingest(name: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one stream's ingest configuration by name: the lifecycle mode +
            retention, the processing-step and routing counts, and the managed
            field schema (a map of field name to type — populated for 'wired'
            streams, empty for 'classic' ones). Errors if the stream does not
            exist."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_stream_ingest(name))

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def enable_streams() -> dict[str, Any]:
            """Enable the wired Streams framework (creates the root streams +
            backing ES resources). Idempotent: a no-op ('noop') when already
            enabled. (Kibana Streams is Tech-Preview.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.enable_streams())

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def resync_streams() -> dict[str, Any]:
            """Rebuild the Elasticsearch assets (index/component templates, ingest
            pipelines) backing all streams from their stored definitions. Use when
            assets have drifted. (Tech-Preview.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.resync_streams())

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def fork_stream(
            parent_name: Annotated[str, Field(min_length=1)],
            child_name: Annotated[str, Field(min_length=1)],
            condition_field: Annotated[str, Field(min_length=1)],
            condition_value: Annotated[str, Field(min_length=1)],
        ) -> dict[str, Any]:
            """Fork a wired stream: create `child_name` (which MUST start with
            `parent_name` + '.') and route documents where `condition_field` ==
            `condition_value` into it. Created STAGED (status=disabled): no live
            routing until activated in the Kibana UI. Fails if the child already
            exists. (Tech-Preview.)"""
            parent_name, child_name = parent_name.strip(), child_name.strip()  # match delete's normalization
            if not child_name.startswith(parent_name + "."):
                raise ToolError(f"child_name must start with '{parent_name}.'")
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(
                    gw.fork_stream(parent_name, child_name, condition_field, condition_value))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def set_stream_processing(
            name: Annotated[str, Field(min_length=1)],
            steps: list[dict[str, Any]],
            confirm: bool = False,
        ) -> dict[str, Any]:
            """Replace a stream's ingest processing steps (a whole-list replace,
            not a merge/append) — e.g. [{"grok": {...}}, {"set": {...}}] in the
            Kibana Streams processing-step shape; an empty list clears all
            processing. Applies to both wired and classic streams (processing is
            a generic ingest facet). Errors if the stream does not exist.
            (Tech-Preview.)"""
            if not steps and not confirm:
                raise ToolError(
                    "steps=[] clears ALL processing steps and cannot be undone; pass confirm=True"
                )
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.set_stream_processing(name=name, steps=steps))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def deactivate_fork(
            parent: Annotated[str, Field(min_length=1)],
            child: Annotated[str, Field(min_length=1)],
        ) -> dict[str, Any]:
            """Deactivate a forked child stream: stop routing NEW documents from
            `parent` into `child` going forward. Documents already routed into
            `child` are untouched — this only flips the routing rule's status, it
            does not move data. Errors if `child` is not a routing entry of
            `parent`. (Tech-Preview.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.deactivate_fork(parent=parent, child=child))

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def set_stream_retention(
            name: Annotated[str, Field(min_length=1)],
            retention: Annotated[str, Field(pattern=r"^\d+(d|h|m|s|ms|micros|nanos)$")],
        ) -> dict[str, Any]:
            """Set a stream's DSL data retention (e.g. '30d', '24h'). DESTRUCTIVE:
            shortening retention ages out and deletes documents older than the new
            window. Converts an inherit/disabled lifecycle to explicit DSL,
            permanently decoupling the stream from its parent's lifecycle. Refuses a
            stream on an ILM policy. Last-writer-wins (no optimistic concurrency).
            (Tech-Preview.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.set_stream_retention(name, retention))

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def activate_fork(
            parent: Annotated[str, Field(min_length=1)],
            child: Annotated[str, Field(min_length=1)],
            confirm: bool = False,
        ) -> dict[str, Any]:
            """Activate a forked child stream: start routing matching documents
            from `parent` into `child` LIVE. DESTRUCTIVE: diverts live documents
            out of `parent` into `child` going forward (documents already routed
            into `child` before a later deactivate stay there — this does not
            move data back). Requires confirm=True. Errors if `child` is not a
            routing entry of `parent`. (Tech-Preview.)"""
            if not confirm:
                raise ToolError(
                    "activate_fork diverts live documents into the child stream; pass confirm=True")
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.activate_fork(parent=parent, child=child))

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_stream(
            name: Annotated[str, Field(min_length=1)], force: bool = False
        ) -> dict[str, Any]:
            """Delete a stream. DESTRUCTIVE: removes the definition AND its backing
            data stream; for a parent, CASCADE-deletes the whole subtree + data.
            Refuses a root stream or a parent-with-children unless force=True. The
            children check reads the stream list then deletes (a small TOCTOU
            window: a child forked in between is missed) — the destructive tier +
            deliberate intent is the backstop. (Tech-Preview.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.delete_stream(name, force))

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def disable_streams(confirm: bool = False) -> dict[str, Any]:
            """Disable the wired Streams framework. DESTRUCTIVE: deletes ALL wired
            stream definitions and their data cluster-wide (classic-stream data is
            preserved). Requires confirm=True. Recover with enable_streams (recreates
            EMPTY roots only — forked children and their data are NOT restored).
            (Tech-Preview.)"""
            if not confirm:
                raise ToolError(
                    "disable_streams deletes all wired streams + data; pass confirm=True")
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.disable_streams())
