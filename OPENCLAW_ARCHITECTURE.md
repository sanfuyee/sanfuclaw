# OpenClaw Architecture Deep Dive

## Overview
OpenClaw is a **local-first personal AI assistant** that runs on your own devices and connects to 20+ messaging platforms. It emphasizes privacy, local execution, and unified access across multiple communication channels.

## Core Architecture

### Hub-and-Spoke Model
```
Messaging Platforms (WhatsApp, Telegram, Slack, Discord, etc.)
              ↓
        ┌─────────────┐
        │   Gateway   │  (WebSocket control plane @ ws://127.0.0.1:18789)
        └─────────────┘
         /     |      \
        /      |       \
    Agents  Tools  Clients (CLI, WebChat, macOS/iOS/Android)
```

### Three Main Layers

#### 1. **Control Plane: Gateway** (`/src/gateway`)
- **Role**: Central hub coordinating all operations
- **Technology**: WebSocket server running locally on port 18789
- **Responsibilities**:
  - Message routing from platforms to agents
  - Session management and lifecycle
  - Authentication and authorization (DM pairing policies)
  - Tool invocation and resolution
  - Hook system for events
  - HTTP handling and MCP (Model Context Protocol)

**Key Components**:
- `server.ts` / `server.impl.ts` - Main server implementation
- `server-ws-runtime.ts` - WebSocket runtime handling
- `server-channels.ts` - Channel management
- `server-chat.ts` - Chat message handling
- `session-lifecycle-state.ts` - Session management
- `auth.ts` - Authentication logic
- `tools-invoke-http.ts` - Tool execution
- `hooks.ts` - Event hook system

#### 2. **Agents & Execution** (`/src/agents`)
- **Role**: Process messages and execute tasks
- **Execution Models**:
  - **Pi Agent**: RPC mode with tool streaming and block-level streaming
  - **Embedded Runner**: Local execution environment (`pi-embedded-runner.ts`)
  - **CLI Runner**: Command-line execution (`cli-runner.ts`)
  - **Bash Tools**: Shell command execution

**AI Model Integration**:
- Anthropic API (`anthropic-transport-stream.ts`)
- OpenAI API (`openai-transport-stream.ts`)
- Google models (`google-transport-stream.ts`)
- Model fallback logic for reliability

**Key Features**:
- Command processing (`agent-command.ts`)
- Skill management (`/skills` directory)
- Tool resolution and invocation
- Model catalog management
- Authentication health monitoring

#### 3. **Channels: Platform Integration** (`/src/channels`)
- **Role**: Integrate with messaging platforms
- **Supported Platforms**:
  - Chat: Slack, Telegram, WhatsApp, Discord, Teams, Signal, Zalo, WeChat, etc.
  - Protocols: Matrix, IRC, Mattermost, Feishu
  - Special: iMessage (via BlueBubbles)
  - Web: WebChat UI

**Channel Features**:
- Transport layer abstraction
- Session binding and context
- Allowlist management (access control)
- Thread binding policies
- Typing indicators and draft streams
- Reaction acknowledgments
- Model overrides per channel

## Data Flow

### Message Processing Pipeline
```
1. User sends message on Platform (e.g., Slack)
                ↓
2. Channel Transport receives message
                ↓
3. Gateway routes to appropriate Agent
                ↓
4. Agent processes with AI model (Claude, GPT, etc.)
                ↓
5. If tools needed → Agent invokes Tools
                ↓
6. Response generated
                ↓
7. Gateway sends back to Channel
                ↓
8. Channel delivers to Platform
```

### Tool Invocation Flow
```
Agent → Tool Resolution → Tool Execution → Response
         ├─ Identify required tools
         ├─ Resolve from registry
         └─ Stream results back
```

## Key Subsystems

### 1. **Security Model**
- **Default**: DM Pairing policy - unknown senders get pairing code
  - Configuration: `dmPolicy="pairing"`
- **Open Mode**: Allow all messages
  - Configuration: `dmPolicy="open"` + `"*"` in allowlist
- **Authentication Profiles** (`/agents/auth-profiles`)
  - Channel-specific credentials
  - Device authentication tokens

### 2. **Tool System**
- **Skills Platform**: Bundled, managed, workspace-level
- **Tool Types**:
  - Browser Control (dedicated Chrome/Chromium)
  - Shell Commands (bash-tools.ts)
  - Web Tools (fetch, search)
  - Media Tools (image gen, video gen, music gen, TTS)
  - Device Actions (camera, screen recording, location via nodes)
  - Webhooks and Cron automation
  - Session Tools (agent-to-agent coordination)

### 3. **Voice Capabilities**
- **macOS/iOS**: Wake word detection
- **Android**: Continuous voice mode
- **TTS**: ElevenLabs with fallback

### 4. **Canvas & Visual Interface**
- **A2UI (Agent-to-UI)**: Dynamic visual interactions
- **Live Canvas**: Agent-driven workspace

### 5. **Device Nodes**
- Extended execution capabilities
- Local actions (camera, screen, location)
- Device-specific functionality
- Communicate via `node.invoke` RPC

## Session Management

### Session Lifecycle
```
Session Creation → Channel Binding → Message Processing → History Tracking
     ↓                                       ↓
Config Initialization         Tool execution & streaming
     ↓                                       ↓
Auth Setup                      Response generation
     ↓                                       ↓
Initial Context              Session state update
```

### Session Features
- Per-session toggles:
  - Thinking level (reasoning depth)
  - Verbose mode (output detail)
  - Model selection
- History tracking and state management
- Multi-agent routing (different channels → different agents)

## Configuration System

### Runtime Configuration
- Multi-agent routing
- Channel allowlists
- Model overrides
- Workspace settings
- Device capabilities

### Onboarding
- `openclaw onboard --install-daemon`
- Guided configuration wizard
- Gateway setup
- Workspace initialization
- Channel connection
- Skill installation

## Communication Protocols

### WebSocket (Primary)
- Clients connect to Gateway via WebSocket
- Real-time message streaming
- Tool execution and response streaming
- Block-level streaming for incremental responses

### HTTP (Secondary)
- MCP HTTP endpoints
- Tool invocation via HTTP
- Webhook support
- API access

## Testing Infrastructure

- Extensive test coverage throughout
- Integration tests (`.integration.test.ts`)
- Live tests (`.live.test.ts`)
- Test helpers and utilities
- Test fixtures for different platforms

## Deployment Options

### Local Deployment
- Node.js 24 or Node 22.16+
- NPM installation: `npm install -g openclaw@latest`
- Runs daemon in background

### Remote Gateway
- Run Gateway on Linux instance
- Clients connect via:
  - Tailscale Serve/Funnel (automated HTTPS)
  - SSH Tunnels (manual secure access)
- Device nodes execute locally

## Development Channels

- **Stable**: `vYYYY.M.D` tagged releases, npm `latest`
- **Beta**: `vYYYY.M.D-beta.N` prerelease, npm `beta`
- **Dev**: Main branch, npm `dev` (when published)

## Key Design Principles

1. **Local-First**: Computation happens on user's devices
2. **Multi-Channel**: Single agent responds across platforms
3. **User Control**: No cloud dependency, privacy-focused
4. **Modular**: Pluggable channels, tools, and skills
5. **Extensible**: Custom skills, plugins, and integrations
6. **Secure By Default**: Pairing policies, allowlists
7. **Streaming**: Incremental responses (block-level streaming)

## Build Approach for Python Version

Based on this architecture, a Python implementation would need:

1. **Gateway Server**
   - WebSocket server (FastAPI + WebSockets)
   - Message routing logic
   - Session management

2. **Agent System**
   - LLM integration (Claude, GPT, etc.)
   - Tool execution framework
   - Response streaming

3. **Channel Adapters**
   - Start with 1-2 platforms (Telegram, Discord)
   - Abstract transport layer
   - Message parsing and serialization

4. **Tool System**
   - Tool registry and resolution
   - Execution sandboxing
   - Result streaming

5. **Storage & State**
   - Session persistence
   - History tracking
   - Configuration management

---

**Sources**: Official OpenClaw GitHub repository and documentation (docs.openclaw.ai)
