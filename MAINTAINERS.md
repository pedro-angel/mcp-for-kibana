# Maintainers

This file defines ownership for `mcp-for-kibana` release and maintenance workflows.

## Current Maintainers

- [@pedro-angel](https://github.com/pedro-angel)

## Responsibilities

- Review and merge pull requests
- Maintain the changelog and versioning
- Approve and monitor releases (the `ghcr.io/pedro-angel/mcp-for-kibana` container
  image; PyPI once packaging lands, tracked in the roadmap)
- Maintain CI/CD, security checks, and dependency policy
- Triage issues and security reports

## Release Ownership

- Releases are cut from protected `main` only.
- Release tags use `vX.Y.Z`.
- Maintainers verify the changelog, the Definition-of-Done gate (`make dod`),
  and artifact integrity before tagging.
