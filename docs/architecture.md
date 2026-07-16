# Architecture

A self-hosted MCP server that lets LLM agents hold real conversations in a Slack
workspace: instant delivery while an agent is listening, durable store-and-forward
when nobody is. This page is the visual companion to [DESIGN.md](../DESIGN.md).

## System overview

```mermaid
flowchart LR
    subgraph CLIENTS["MCP clients"]
        CC["Claude Code<br/>(per-agent bearer token)"]
        WI["WILMA CLI<br/>bridge plugin"]
    end

    subgraph AWS["AWS"]
        MCP["MCP Server Lambda<br/>FastMCP, streamable HTTP<br/>Function URL, 900s"]
        AUTH["bearer middleware<br/>token to agent identity"]
        ING["Ingest Lambda<br/>signature check, dedupe,<br/>name enrichment"]
        DDB[("DynamoDB<br/>messages, cursors, sessions,<br/>agent registry, name cache")]
        EVT["AppSync Events<br/>WebSocket pub/sub<br/>(ephemeral)"]
        SM["Secrets Manager<br/>bot tokens, MCP tokens,<br/>signing secrets"]
    end

    subgraph SLACK["Slack workspace"]
        WAPI["Web API"]
        EAPI["Events API"]
        CH["channels, private<br/>channels, DMs"]
    end

    CC -- "Authorization: Bearer" --> AUTH --> MCP
    WI -- "Authorization: Bearer" --> AUTH
    MCP -- "chat.postMessage<br/>per-agent bot token" --> WAPI --> CH
    CH -- "message events" --> EAPI
    EAPI -- "HMAC-signed POST" --> ING
    ING -- "1. durable write" --> DDB
    ING -- "2. best-effort publish" --> EVT
    EVT -- "push to open<br/>wait_for_messages" --> MCP
    MCP <--> DDB
    MCP --- SM
    ING --- SM
```

Write order is the delivery guarantee: ingest commits to DynamoDB before publishing
to AppSync, so real-time is purely an optimization. If nothing is listening, the
publish evaporates and the message waits in the inbox.

## A live round trip

```mermaid
sequenceDiagram
    autonumber
    actor H as Human
    participant SL as Slack
    participant I as Ingest Lambda
    participant D as DynamoDB
    participant E as AppSync Events
    participant M as MCP Server Lambda
    actor A as Agent

    A->>M: wait_for_messages, up to 840s
    M->>E: subscribe over WebSocket
    M->>D: drain backlog past cursor
    H->>SL: types a message
    SL->>I: event POST, HMAC-signed
    I->>I: verify, dedupe, resolve names
    I->>D: store message, the source of truth
    I->>E: publish a copy
    E->>M: push over WebSocket
    M->>D: advance cursor
    M->>A: wait returns, about 1s after the keystroke
    A->>M: send_message reply
    M->>M: guardrails, turn budget and cooldown
    M->>SL: chat.postMessage as the agent
```

## Trust boundaries

Every boundary defines legitimate traffic the same way: proof that the caller holds
a secret that only legitimate parties were given. The boundaries differ only in
which secret and how possession is proven.

| Boundary | Secret | Proof style | Who holds it |
|---|---|---|---|
| MCP client to server | `McpToken` per agent (+ legacy dev token) | Present the secret (bearer header, constant-time compare) | Operator's machines, Secrets Manager |
| Slack to ingest | Signing secret per app (relay + agent DM events) | HMAC fingerprint over timestamp + body; the secret never travels | Slack, Secrets Manager |
| Server to Slack | Bot token per Slack app | Bearer header to slack.com | Lambdas only (never clients) |
| Lambdas to AppSync | Events API key | Header on publish, subprotocol on subscribe | The two Lambdas (hardening path: IAM signing) |
| Lambdas to AWS services | None stored | IAM execution roles, auto-injected STS credentials | Lambda runtime |

## Slack-side identity model

```mermaid
flowchart TB
    subgraph APPS["N + 1 internal Slack apps"]
        R["relay app<br/>reads + all channel events<br/>(channels, groups, im)"]
        W["agent app per agent<br/>send-only + optional DM events<br/>mention-able bot user"]
    end
    CH2["Workspace conversations"]
    R -- "one event stream,<br/>one signature check" --> CH2
    W -- "posts as the agent" --> CH2
```

Many writers, one reader: each agent has its own revocable face and token, while the
relay is the single event subscriber, so every message is ingested and verified
exactly once no matter how many agents share a channel. Channel membership (the
invite) is the per-conversation opt-in.

## Component inventory

| Component | Service | Responsibility |
|---|---|---|
| `{env}-{project}-mcp-server` | Lambda arm64 + Function URL (streaming) | 7 MCP tools; identity, guardrails, long-poll sessions up to 840s |
| `{env}-{project}-slack-ingest` | Lambda arm64 + Function URL | Multi-app signature verify, dedupe on (channel, ts), name and mention enrichment, channel registry |
| `{env}-{project}-messages` | DynamoDB (on-demand) | Message history (retention configurable), per-identity cursors, sessions, agent registry, name cache |
| `{env}-{project}-events` | AppSync Events API | Ephemeral fan-out: channel per conversation, wildcard subscribe for watch-everything |
| `{Env}-{Project}-*` secrets | Secrets Manager | Every static credential; rotation = overwrite + Lambda bounce |

Idle cost of the whole system: a few dollars a month at personal scale.
