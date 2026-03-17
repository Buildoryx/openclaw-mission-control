# OpenClaw Mission Control Architecture

OpenClaw Mission Control is the management and orchestration layer for your OpenClaw agents. It providing a centralized dashboard to configure, deploy, and monitor agents across one or more OpenClaw Gateways.

## System Components

The system is composed of three primary layers:

### 1. Mission Control Dashboard (Frontend)
- **Tech Stack**: Next.js, React, Tailwind CSS, React Query.
- **Role**: The user interface. It allows you to:
  - Manage **Organizations** and **Members**.
  - Create and configure **Boards** (logical groupings of agents and tasks).
  - Provision and monitor **Agents**.
  - Connect to **Gateways**.
  - Chat directly with agents via a built-in WebChat.

### 2. Mission Control API (Backend)
- **Tech Stack**: FastAPI, SQLModel (SQLAlchemy), PostgreSQL, Redis.
- **Role**: The brain of the management layer. It handles:
  - **CRUD Operations**: Storing configuration for agents, boards, and gateways in the database.
  - **Provisioning**: When an agent is created/updated, the backend generates the necessary configuration templates and pushes them to the **OpenClaw Gateway** via RPC.
  - **Connectivity**: Maintains the connection status to various gateways.
  - **Authentication**: Handles user login via Local Tokens or Clerk.

### 3. OpenClaw Gateway (The Runtime)
- **Role**: The execution environment. It is a separate process (the one you run via `openclaw start`) that:
  - **Runs Agents**: Executes the actual agent logic and LLM calls.
  - **Platform Integrations**: Connects to Telegram, Discord, etc.
  - **Session Management**: Maintains the state of active conversations.
  - **RPC Surface**: Exposes an API that Mission Control uses to control it.

## Core Workflows

### Gateway Integration
Mission Control "claims" a gateway by storing its URL and an **Operator Token**. Secure communication is established via persistent WebSocket/RPC.
- Mission Control periodically checks the gateway's **Health** and **Version Compatibility**.
- It requires `operator.read` and `operator.admin` scopes on the token to manage agents.

### Agent Provisioning
1. **Definition**: You define an agent's personality, model, and tools in the Mission Control UI.
2. **Template Generation**: The Backend generates an OpenClaw-compatible configuration.
3. **Distribution**: The configuration is pushed to the Gateway.
4. **Lifecycle**: The Gateway starts the agent. The agent is configured to send "Heartbeats" back to Mission Control, allowing the dashboard to show it as "Online".

### Communication Flows
- **Dashboard to Agent**: When chatting in the UI, the frontend sends messages to the Backend API, which forwards them to the Gateway via RPC. The Gateway then delivers the message to the specific agent session.
- **Direct Platform (e.g., Telegram)**: When a user messages the bot on Telegram, the Telegram API hits the Gateway directly. The Gateway processes the message with the agent and responds back to Telegram.

## Data Persistence
- **Mission Control DB**: Stores "Management State" (Who owns what? What is the intended configuration?).
- **Gateway Persistence**: Stores "Runtime State" (Active sessions, message history, specific agent files).

---
> [!NOTE]
> Now that your Gateway is connected, you can start creating **Boards** and **Agents** in the dashboard. Mission Control will handle syncing these to your running Gateway automatically.
