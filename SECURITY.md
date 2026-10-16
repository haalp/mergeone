# Security policy

mergeone sits between agents and your default branch, so a bug here can merge the wrong thing.

## Reporting

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/haalp/mergeone/security/advisories/new) on this
repository. Do not open a public issue for security problems. You will get an acknowledgement within
3 working days and a fix or mitigation plan within 14 days for confirmed issues.

## Scope

- The Go gateway (`gate/`): webhook signature verification, policy evaluation, the HTTP API, MCP server,
  gatelog storage.
- The Python reference implementation (`mergeone/`).
- The install script and release artifacts.

Out of scope: third-party model APIs (report to the vendor), the benchmark data, the website.

## What we consider a vulnerability

- A way to obtain a `pass` verdict that the policy should not grant (rule bypass, calibration tampering,
  path-prefix evasion for `always_human_paths`).
- Webhook spoofing, missing or bypassable signature checks.
- Secrets leaking into the gatelog, check-run output, or MCP responses.
- Unauthenticated access to the API or MCP server when configured with a token.

## Design notes

- The gateway **fails closed**: a model error or timeout yields `hold`, never `pass`.
- Verdicts are logged with the hash of the exact rendered input, so any decision can be reproduced.
- No model output is executed; models return probabilities only.

Supported versions: the latest minor release.
