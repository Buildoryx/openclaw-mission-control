# OpenClaw Mission Control Usage Guide

Now that Mission Control is connected to your Gateway, here is a step-by-step guide to getting started.

## 1. Access and Login
- **URL**: [http://localhost:3000](http://localhost:3000)
- **Authentication**: When prompted, enter your `LOCAL_AUTH_TOKEN`:
  ```text
  c03a18617836a8992bc5fdea5db99cf1c6be86d00b67efaa4dbabcb035aed197
  ```

## 2. Navigating the Dashboard
- **Sidebar**: Access your Boards, Agents, and Gateways.
- **Gateways**: Verify the "Default Gateway" is listed as **Connected**.
- **Organization**: Manage members and settings for your workspace.

## 3. Creating a Board
A **Board** is a container for agents working on a specific theme or project.
1. Click on **Boards** in the sidebar.
2. Click **Create Board**.
3. Give it a name (e.g., "Research Hub") and description.
4. Once created, you'll see the Board view where you can add agents.

## 4. Provisioning an Agent
1. Inside a Board, click **New Agent**.
2. **Identity**: Give your agent a name and a "Role" (personality).
3. **Model**: Select the LLM you want to use (e.g., Grok 4).
4. **Tools**: Enable skills like `websearch`, `shell`, or `memory`.
5. **Save**: Click **Deploy** or **Save**. Mission Control will automatically sync this agent to your local OpenClaw Gateway.

## 5. Interacting with Agents
- **WebChat**: Click the "Chat" icon on an agent's card to start a conversation directly from the dashboard.
- **Platforms**: If the agent is configured for Telegram or Discord, it will automatically listen for messages on those platforms once deployed.

## 6. Monitoring and Management
- **Status Peaks**: See real-time "Heartbeats" showing which agents are alive.
- **Activity Log**: View recent logs and events across all agents.
- **Templates**: Export or import agent configurations as JSON templates.

---
> [!TIP]
> Since you have **Supermemory** installed, you can enable the `openclaw-supermemory` skill on your agents to give them persistent, long-term memory!
