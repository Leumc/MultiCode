# Control–worker broker protocol (slice 1)

The control process owns `control.db`. A worker connects to a Unix domain socket and receives only the capabilities below; it must not open the SQLite database.

## Transport and framing

- Unix domain stream socket, default: `<data-dir>/control-worker.sock`.
- One request and one response per connection.
- Each message is one UTF-8 JSON object followed by `\n`.
- The JSON payload (excluding `\n`) is limited to 32 MiB on both client and server.
- Objects use exact schemas: unknown/missing keys, wrong JSON types (including booleans where integers are expected), malformed JSON, extra lines, invalid IDs, and invalid result-state enums are rejected.
- The socket is created with mode `0660`. The server refuses to replace a pre-existing non-socket path.

## Operations

These are the only accepted values of `op`:

```json
{"op":"claim"}
{"op":"cancel-check","job_id":1}
{"op":"complete","job_id":1,"execution":{"status":"completed","compile":{"status":"completed","stdout":"","stderr":"","exit_code":0,"wall_time_ms":1,"output_truncated":false},"cases":[]}}
{"op":"system-error","job_id":1,"message":"runner failure"}
```

A successful claim returns only runner-required fields: `id`, `filename`, `source`, `compiler`, `time_limit_ms`, `memory_limit_mib`, and case `input_text` values. User records, grants, audit data, and other database fields are never exposed.

Responses are exact objects: `{ "ok": true, ... }`, or `{ "ok": false, "error": "..." }`. Protocol errors do not include exception details.

## Peer authorization

On Linux the default identity provider obtains kernel-authenticated `pid`, `uid`, and `gid` through `SO_PEERCRED`. `serve --worker-uid UID` selects authorized worker UIDs and is repeatable; if omitted, only the control process UID is accepted. Both identity provider and authorization callback are injectable for deterministic tests.

Filesystem ownership/group setup for separate production service users is intentionally outside this slice; no root deployment changes are included.

## Python boundary

`JobWorker` depends on the runtime-checkable `JobBroker` protocol. `UnixJobBrokerClient` is the production-side adapter. `DatabaseJobBroker` remains available only for control-side dispatch and in-process tests, preserving existing test compatibility.

## Current trust boundary and follow-up risks

An authorized worker UID is trusted to report completion for a positive job ID. This first slice does not yet issue per-claim lease tokens, enforce claim ownership, retry transient broker outages, or provide protocol version negotiation. Those should be addressed before supporting multiple mutually untrusted workers.
