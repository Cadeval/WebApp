# Security policy

Report suspected Cadevil vulnerabilities privately to
[mia@meanderingmind.me](mailto:mia@meanderingmind.me). A deployed instance can
publish an operator-specific contact at `/.well-known/security.txt`; use that
contact when the issue concerns that deployment.

Include the affected version, a short description of the impact, the route or
component involved, and a minimal reproduction using synthetic data. For
application failures, include the response's `X-Request-ID` if available.
Do not send passwords, tokens, private signing keys, personal data or customer
IFC files. Ask for an agreed secure transfer channel before sending sensitive
attachments; no public encryption key is advertised here.

Please allow the maintainer to investigate and coordinate a fix before sharing
exploit details publicly. Response and fix dates depend on the issue and are
agreed during triage; this policy does not promise a response deadline or a
paid bounty. Test only instances and accounts you are authorized to assess.

Security fixes target the current `0.14.x` release and the current development
branch. Older versions require an upgrade unless the maintainer explicitly
agrees to a backport. Installed third-party plugins and deployment-specific
database/cache drivers need their own updates and review.

## Release evidence

- [Code audit and corrections](docs/CODE_AUDIT.md) documents the `0.11.0` audit
  and its limits.
- [SBOM scope and regeneration](docs/SBOM.md) describes the versioned
  CycloneDX dependency inventories. The application inventory is published at
  `/security/sbom.json`.
- [Logging](docs/LOGGING.md) describes correlation IDs, redaction and access to
  live logs. A successful static or dependency scan is not a security guarantee.

## Deployment

Serve the application and `/.well-known/security.txt` over HTTPS in production.
Keep the disclosure contact and `Expires` date current; the committed file
expires on **4 January 2027** and is not silently renewed by a request.
`SECURITY_TXT_CONTACT`, `SECURITY_TXT_EXPIRES`, `SECURITY_TXT_CANONICAL` and
`SECURITY_TXT_POLICY` can override deployment metadata. Canonical and policy
URLs must use HTTPS; omit them until the actual public hostname is known.
The underscore spelling `/.well_known/security.txt` and root `/security.txt`
redirect to the standard location.

Keep uploaded models and reports private: do not expose `MEDIA_ROOT` through a
proxy alias. Restrict admin log access, maintain strong secrets and trusted
origins, and disable debug and every development MCP in production. The
development audit MCP works locally and does not send source or model content
to a hosted scanner.
