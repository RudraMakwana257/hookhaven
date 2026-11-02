# SSRF threat model

Hookhaven makes outbound HTTP requests to URLs chosen by users. Without controls, that is a
server-side request forgery primitive: a user could make *us* call cloud metadata
(`169.254.169.254`), loopback services, or private-network hosts.

## Controls

| # | Control | Where | Stops |
|---|---------|-------|-------|
| 1 | Scheme allowlist (`https` only in production), port allowlist, no URL credentials, no whitespace/control chars/backslashes, length cap | `core/ssrf.py` | `file://`, `gopher://`, `user@host` parser confusion, header injection, non-web ports (22, 6379, 5432) |
| 2 | Resolve the hostname and judge the **IPs**, never the string | `core/ssrf.py` | `127.1`, `2130706433`, `0x7f.1`, `0177.0.0.1`, `localhost`, DNS names pointing at private IPs |
| 3 | **Every** resolved address must be public | `core/ssrf.py` | Mixed A-records (one public, one private) |
| 4 | Explicit block of special ranges + unwrapping of IPv6 forms | `core/ssrf.py` | `::ffff:127.0.0.1`, NAT64 `64:ff9b::/96`, 6to4, Teredo, `fd00:ec2::254`, CGNAT, multicast, reserved |
| 5 | Re-validate at **send time** and connect to the validated IP; TLS SNI and `Host` keep the original hostname | `core/safe_http.py` | DNS rebinding (record flips to a private IP after registration) |
| 6 | `follow_redirects=False` | `core/safe_http.py` | Open-redirect bounce to an internal URL |
| 7 | `trust_env=False` | `core/safe_http.py` | `HTTP_PROXY` env silently bypassing IP pinning |
| 8 | Response body read is capped; overall deadline in addition to per-phase timeouts | `core/safe_http.py` | Memory exhaustion / slow-loris style responses |
| 9 | Production refuses to boot with `ALLOW_PRIVATE_DESTINATIONS=true` | `core/config.py` | Dev escape hatch leaking into prod |

## Residual risk / follow-ups
- Network-level egress filtering (a firewall rule denying RFC1918 + metadata from the worker)
  should back this up in deployment. Application checks are defence in depth, not the only wall.
- Error messages say *why* a URL was rejected; acceptable for authenticated users, but worth
  revisiting if endpoint registration is ever exposed unauthenticated.
