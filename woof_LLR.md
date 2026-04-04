# Woof Low-Level Requirements

This document captures the requirements for Woof, the user-facing controller component of OuEstCharlie. For Woof's role in the overall architecture, see [HLD.md § Woof](../HLD.md#woof-user-facing-application-and-controller).

## Agent Lifecycle Management

- Woof shall act as an MCP client, communicating with agents via the Model Context Protocol (see [controller_api.json](../controller_api.json) for tool definitions)
- Woof shall support two MCP transports: stdio for child process agents (default), Streamable HTTP for networked agents
- Woof shall track each agent run through a state machine: `pending → running → completed | failed | timeout`
- Woof shall detect stuck agents via progress token timeout (default: 5 minutes without `notifications/progress`) and transition them to `timeout` state
- Woof shall revoke the scoped token of any agent that transitions to `timeout` or is cancelled by the user
- Woof shall cancel running tool calls via `notifications/cancelled` when an agent is timed out or user-cancelled
- Woof shall chain dependent agents automatically (e.g., ingestion → housekeeping → enrichment) without user intervention once the initial trigger is approved

## Agent Progress Reporting

- Agents shall report progress via MCP `notifications/progress` on the tool call's progress token
- Each progress notification shall include: partition being processed, items processed, total items, and current operation (via the `message` field)
- Agents shall report completion as a tool call result with a structured summary: photos processed, errors encountered, artifacts written
- Agents shall report failure as a tool call error with actionable context: which photo, which operation, what error
- Agents shall report non-fatal per-photo errors via MCP `notifications/message` (log level: error) without aborting the run

## Observability

### Activity Log

- Woof shall maintain a device-local activity log (`~/.ouestcharlie/activity.json`) recording all agent runs
- Each entry shall record: agent type, agent ID, backend, partition scope, start time, end time, status, summary stats, and error details
- Woof shall prune activity log entries older than a configurable retention period (default: 30 days)
- The activity log is disposable — loss of the log has no impact on data integrity

### User Surface

- Woof shall expose the following observability surfaces to the UI layer:
  - **Activity feed**: current and recent agent runs with real-time status and progress
  - **Partition health**: per-partition indicators — last housekeeping run, pending dirty changes, missing thumbnails, enrichment coverage percentage
  - **Error drill-down**: per-error detail showing which photo/partition failed, which agent, and the error message

### Alerting

- Woof shall surface agent failures and timeouts to the user via the UI (notification or status indicator)
- Woof shall not require external alerting infrastructure — all observability is self-contained

## Configuration Ownership

- Woof shall own the device-local configuration directory (`~/.ouestcharlie/`):
  - `config.json` — backend connection info
  - `albums.json` — album definitions (saved filters)
  - `activity.json` — agent run history
  - OS keychain entries — master credentials

## Credential Management

- Woof shall store master credentials (S3 IAM keys, OAuth refresh tokens, service account keys) in the OS keychain
- Woof shall never expose master credentials to agents
- Woof shall mint scoped, short-lived tokens for each agent run, restricted to the approved grants
- Woof shall support token revocation for cancelled or timed-out agents

## Change Detection Orchestration

- Woof shall manage the change detection pipeline (triggers + sweep) as described in [HLD.md § Change detection](../HLD.md#change-detection)
- Woof shall maintain a dirty partition queue with debounce logic (default: 10 minutes quiet period)
- Woof shall schedule housekeeping agents for dirty partitions after the debounce window expires

## Gallery Photo Sharing

- The gallery shall allow the user to select one or more photos and share them into the active MCP Host conversation
- Photo binaries are served on demand by a Woof MCP resource, fetched from Wally when the host resolves the resource link
- The gallery shall limit share actions to a maximum of 10 photos at a time
- The share button shall be disabled when the gallery is accessed outside a Claude Desktop host (i.e. no MCP Apps connection is established)

## User Approval

- Woof shall present agent scope requests to the user for explicit approval before issuing credentials
- Once approved, Woof may trigger subsequent runs of the same agent type within the approved grants without further confirmation
