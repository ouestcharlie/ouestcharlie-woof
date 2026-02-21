# OuEstCharlie — Woof

Low-level design documents for **Woof**, the central controller of OuEstCharlie.

Woof runs on the user's device and serves two roles:
1. **UI backend** — serves the photo browsing experience
2. **MCP client** — orchestrates stateless agents via the Model Context Protocol

## Documents

| Document | Purpose |
|----------|---------|
| [woof_LLR.md](woof_LLR.md) | Low-level requirements: agent lifecycle, progress reporting, health indicators |
| [woof_LLD.md](woof_LLD.md) | Low-level design: MCP client, timeout detection, agent chaining, activity log |
| [woof_LLD_rationale.md](woof_LLD_rationale.md) | Design rationale and alternatives considered |

## Context

This is one of three repositories for the OuEstCharlie project:

| Repository | Purpose |
|------------|---------|
| [ouestcharlie](../ouestcharlie) | Architecture docs, HLR/HLD, MCP interface, project charter |
| **ouestcharlie-woof** *(this repo)* | Woof controller — LLR, LLD, rationale |
| [ouestcharlie-py-toolkit](../ouestcharlie-py-toolkit) | Python toolkit for building agents |

See [ouestcharlie/HLD.md](../ouestcharlie/HLD.md) for the overall system architecture.
