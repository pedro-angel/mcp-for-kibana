# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | ✅ Current          |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please open a [private security advisory](https://github.com/pedro-angel/mcp-for-kibana/security/advisories/new)
on GitHub, or contact the maintainer [@pedro-angel](https://github.com/pedro-angel)
directly via GitHub.

Please include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We aim to acknowledge reports within a few days (best effort — this is a
solo-maintained project) and to provide an assessment within two weeks.

## Security Model

mcp-for-kibana is a **credential-brokering** server: it holds no long-lived secrets
of its own and never stores Kibana credentials. Each request carries the
caller's own Kibana API key, so Kibana RBAC and audit logging stay per-user, and
the server is stateless. Understanding this model matters for a secure
deployment:

- **API keys are per-request and never logged.** In HTTP mode the key travels in
  the `Authorization` header; the server passes it to Kibana and never persists
  or logs it.
- **Write safety is a server-side boundary.** Every tool is classified
  `read` / `write` / `destructive`. `KIBANA_MCP_TIER` caps which tiers are
  visible, and a call to a tool above the cap is *rejected*, not merely hidden
  from the listing. Cap the tier to the least privilege a deployment needs.
- **The Kibana API key is the real authority.** Scope the key itself (Kibana
  roles/spaces) as the primary control; the tier cap is defense in depth, not a
  substitute for a least-privilege key.

## Security Best Practices for Users

- **Never hardcode credentials.** Use environment variables, a per-request
  `Authorization` header, or a secrets manager — never commit a key.
- **Use API keys**, scoped to the minimum Kibana privileges the deployment needs,
  instead of `elastic` superuser credentials.
- **Enable TLS** for all connections to Kibana, and terminate TLS in front of the
  server when running the streamable-HTTP mode for multiple users.
- **Cap the write tier** (`KIBANA_MCP_TIER=read` where a deployment only needs
  reads) so a compromised or misbehaving model cannot reach destructive tools.
- **Keep dependencies updated.** Dependabot proposes bumps; `make audit`
  (pip-audit) and `make sast` (bandit) gate every change.
- **Review DEBUG-level logs** before enabling them — request metadata may be
  sensitive.

## Coordinated disclosure

Please allow up to 90 days before public disclosure. Reporters are credited in
the release notes unless they ask otherwise.

## Scope notes (documented design choices, not vulnerabilities)

- The MCP endpoint performs no transport authentication; deployments must put
  network access control (a proxy) in front of HTTP mode. Kibana authorization
  happens per request with the caller's own API key.
- The tier cap is defense in depth; the Kibana API key's own privileges are the
  primary control.
- Tool results are untrusted content (see below).

## Untrusted content

Tool results can contain text written by anyone who can write to your
Elastic stack — alert names, case comments, log documents, dashboard titles.
That text flows into the model's context, where it can attempt to steer the
agent (prompt injection). If the server is pointed at data written by third
parties, cap the tier (`KIBANA_MCP_TIER=read`) and treat write/destructive
tiers as human-in-the-loop only. Returned documents may also contain PII,
which leaves your deployment with whatever model the client uses.
