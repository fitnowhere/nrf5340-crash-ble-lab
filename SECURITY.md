# Security policy

## Status

This repository is an experimental development reference, not a production
security boundary. Do not expose the reference DK or backend directly to an
untrusted network.

## Important controls and limitations

- Backend API/UI access uses one shared token. Use loopback or a TLS reverse
  proxy with a real identity layer; there is no SSO, RBAC or tenant isolation.
- Uploaded archives and ELFs are untrusted parser input. Size/count/time limits
  and a non-root container reduce risk, but production use should isolate
  symbolization in a separate sandboxed worker.
- The reference app's MCUmgr filesystem is read-only and path-allowlisted.
  Reads are not authenticated. Destructive GATT commands are default-off but
  explicitly enabled by this bench application's `prj.conf`.
- Build fingerprints and SHA-256 values detect mismatches; they are not signed
  device identity or attestation.
- Raw fault capture can fail during concurrent flash activity or power loss.

Never store secrets in diagnostic logs or this reference image. Rotate any
token accidentally committed or disclosed.

## Reporting

Report vulnerabilities privately through GitHub Security Advisories for the
repository rather than a public issue. Include affected commit, reproduction,
impact and suggested mitigation. Do not include production credentials or
sensitive device data.
