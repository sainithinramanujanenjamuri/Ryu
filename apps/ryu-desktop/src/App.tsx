import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowUp,
  Bot,
  ChevronLeft,
  ChevronRight,
  Compass,
  Key,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Shield,
  Sparkles,
  Terminal,
  User,
  Zap,
} from "lucide-react";
import { api } from "./api/client";
import { ApprovalCard } from "./components/ApprovalCard";
import { AttentionGauge } from "./components/AttentionGauge";
import { AuditView } from "./components/AuditView";
import { CommandPalette } from "./components/CommandPalette";
import { MarkdownMessage } from "./components/MarkdownMessage";
import { PulseTimeline } from "./components/PulseTimeline";
import { SlashCommand, SlashCommandMenu } from "./components/SlashCommandMenu";
import { TaskListView } from "./components/TaskListView";
import {
  ApprovalRequestData,
  AttentionState,
  PulseEvent,
  SpaceInfo,
  TaskItem,
} from "./types";

interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  goalId?: string;
  singleAgentEligible?: boolean;
  requiredCapabilities?: string[];
  status?: "loading" | "completed" | "error";
}

export const App: React.FC = () => {
  const [spaces, setSpaces] = useState<SpaceInfo[]>([
    { space_id: "default", name: "Default Space", status: "active" },
  ]);
  const [currentSpaceId, setCurrentSpaceId] = useState("default");
  const [isOnline, setIsOnline] = useState(false);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  const [attention, setAttention] = useState<AttentionState | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequestData[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [auditEvents, setAuditEvents] = useState<PulseEvent[]>([]);
  const [streamPulses, setStreamPulses] = useState<PulseEvent[]>([]);

  // Chat Conversation State
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputPrompt, setInputPrompt] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Slash commands
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");

  // Layout Controls
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [drawerTab, setDrawerTab] = useState<"attention" | "stream" | "tasks" | "audit">("attention");

  // Modals & Settings
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [tokenInput, setTokenInput] = useState(api.getDaemonToken());

  // Live LLM Generation State
  const [liveLLMEnabled, setLiveLLMEnabled] = useState<boolean>(() => {
    return localStorage.getItem("ryu_live_llm") === "true";
  });
  const [llmProvider, setLlmProvider] = useState<string>("ollama");
  const [llmBaseUrl, setLlmBaseUrl] = useState<string>("http://localhost:11434");
  const [llmModel, setLlmModel] = useState<string>("qwen3.5:4b");
  const [llmApiKey, setLlmApiKey] = useState<string>("");

  const toggleLiveLLM = async () => {
    const nextState = !liveLLMEnabled;
    setLiveLLMEnabled(nextState);
    localStorage.setItem("ryu_live_llm", String(nextState));
    try {
      await api.setLLMConfig({ enabled: nextState });
      setMessages((prev) => [
        ...prev,
        {
          id: `cmd-${Date.now()}`,
          role: "system",
          content: nextState
            ? `### ⚡ Live LLM Generation: ON\nPrompts will now be routed to **${llmProvider.toUpperCase()}** (\`${llmModel}\` at \`${llmBaseUrl}\`).`
            : `### 🔒 Deterministic Fast Mode: ON\nPrompts will now use local fast templates (zero network/token overhead).`,
          timestamp: Date.now(),
        },
      ]);
    } catch (e: any) {
      console.error("Failed to sync LLM config with daemon:", e);
    }
  };

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll chat stream
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isSubmitting]);

  // Periodic state refresh
  const refreshState = async () => {
    const t0 = performance.now();
    try {
      await api.checkHealth();
      setLatencyMs(Math.round(performance.now() - t0));
      setIsOnline(true);

      const sps = await api.listSpaces();
      if (sps && sps.length > 0) setSpaces(sps);

      const att = await api.getAttention(currentSpaceId);
      setAttention(att);

      const apps = await api.listApprovals(currentSpaceId);
      setApprovals(apps);

      const tks = await api.listTasks(currentSpaceId);
      setTasks(tks);

      const audit = await api.getAudit(currentSpaceId);
      setAuditEvents(audit);

      try {
        const llmCfg = await api.getLLMConfig();
        if (llmCfg) {
          setLiveLLMEnabled(llmCfg.enabled);
          if (llmCfg.provider) setLlmProvider(llmCfg.provider);
          if (llmCfg.base_url) setLlmBaseUrl(llmCfg.base_url);
          if (llmCfg.model) setLlmModel(llmCfg.model);
        }
      } catch {}
    } catch {
      setIsOnline(false);
      setLatencyMs(null);
    }
  };

  useEffect(() => {
    refreshState();
    const interval = setInterval(refreshState, 3000);
    return () => clearInterval(interval);
  }, [currentSpaceId]);

  // SSE Pulse Stream
  useEffect(() => {
    const unsubscribe = api.subscribeEvents(
      currentSpaceId,
      (pulse) => {
        setStreamPulses((prev) => [...prev.slice(-99), pulse]);
      },
      () => {
        // SSE connection dropped
      }
    );
    return () => unsubscribe();
  }, [currentSpaceId]);

  // Keyboard shortcut Ctrl+K
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setIsPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Handle Input Changes & Slash Detection
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInputPrompt(val);

    if (val.startsWith("/")) {
      setShowSlashMenu(true);
      setSlashFilter(val);
    } else {
      setShowSlashMenu(false);
    }
  };

  // Submit Prompt or Slash Command
  const handleSend = async (overridePrompt?: string) => {
    const promptToSend = (overridePrompt || inputPrompt).trim();
    if (!promptToSend || isSubmitting) return;

    setInputPrompt("");
    setShowSlashMenu(false);

    // Handle Slash Commands
    if (promptToSend.startsWith("/")) {
      handleSlashCommand(promptToSend);
      return;
    }

    // Conversational Prompt
    const userMsgId = `user-${Date.now()}`;
    const assistantMsgId = `asst-${Date.now()}`;

    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: "user",
        content: promptToSend,
        timestamp: Date.now(),
      },
      {
        id: assistantMsgId,
        role: "assistant",
        content: "Analyzing goal and evaluating SCCA capability contracts...",
        timestamp: Date.now(),
        status: "loading",
      },
    ]);

    setIsSubmitting(true);

    try {
      const res = await api.sendPrompt(currentSpaceId, promptToSend, liveLLMEnabled);

      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMsgId
            ? {
                ...msg,
                content: res.response,
                goalId: res.goal_id,
                singleAgentEligible: res.single_agent_eligible,
                requiredCapabilities: res.required_capabilities,
                status: "completed",
              }
            : msg
        )
      );

      // Refresh governance state immediately to capture any pulses or gates
      refreshState();
    } catch (err: any) {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMsgId
            ? {
                ...msg,
                content: `### Execution Error\n\nFailed to process prompt: ${err.message || err}`,
                status: "error",
              }
            : msg
        )
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSlashCommand = (cmdStr: string) => {
    const tokens = cmdStr.split(" ");
    const root = tokens[0].toLowerCase();

    if (root === "/clear") {
      setMessages([]);
      return;
    }

    if (root === "/status") {
      setDrawerOpen(true);
      setDrawerTab("attention");
      setMessages((prev) => [
        ...prev,
        {
          id: `cmd-${Date.now()}`,
          role: "system",
          content: `### SCCA Runtime Status\n- **Space**: \`${currentSpaceId}\`\n- **Daemon**: ${isOnline ? "ONLINE (127.0.0.1:8420)" : "OFFLINE"}\n- **Attention Concurrency Limit ($N$)**: \`${attention?.concurrency_limit || 3}\`\n- **Active Human Gates**: \`${attention?.active_count || 0}\`\n- **Queued Gates**: \`${attention?.queued_count || 0}\``,
          timestamp: Date.now(),
        },
      ]);
      return;
    }

    if (root === "/approvals" || root === "/approval") {
      setDrawerOpen(true);
      setDrawerTab("attention");
      return;
    }

    if (root === "/spaces" || root === "/space") {
      const spaceList = spaces.map((s) => `- \`${s.space_id}\` (${s.name || s.space_id}) ${s.space_id === currentSpaceId ? "**[ACTIVE]**" : ""}`).join("\n");
      setMessages((prev) => [
        ...prev,
        {
          id: `cmd-${Date.now()}`,
          role: "system",
          content: `### SCCA Isolated Spaces\n\n${spaceList}\n\n*Use the Space dropdown in the top-left or sidebar to switch spaces.*`,
          timestamp: Date.now(),
        },
      ]);
      return;
    }

    if (root === "/stream") {
      setDrawerOpen(true);
      setDrawerTab("stream");
      return;
    }

    if (root === "/tasks" || root === "/task") {
      setDrawerOpen(true);
      setDrawerTab("tasks");
      return;
    }

    if (root === "/audit") {
      setDrawerOpen(true);
      setDrawerTab("audit");
      return;
    }

    if (root === "/help") {
      setMessages((prev) => [
        ...prev,
        {
          id: `cmd-${Date.now()}`,
          role: "system",
          content: `### RYU AI Developer Interaction Commands\n\n- **Natural Prompts**: Type any instruction (e.g. \`write basic python program\`).\n- **SCCA §18 Fast Path**: Simple instructions are stamped \`single_agent_eligible: true\` to execute without multi-agent team overhead.\n- **/status**: Inspect space health and dynamic attention budget ($N$).\n- **/spaces**: List isolated spaces and active workspace.\n- **/approvals**: View pending capability approval gates.\n- **/stream**: Live pulse event timeline.\n- **/tasks**: Inspect execution plan DAG.\n- **/audit**: View causal ancestor audit trees.\n- **/clear**: Clear conversational history.`,
          timestamp: Date.now(),
        },
      ]);
      return;
    }

    // Default unknown command
    setMessages((prev) => [
      ...prev,
      {
        id: `cmd-${Date.now()}`,
        role: "system",
        content: `Unknown slash command: \`${root}\`. Type \`/help\` for available commands.`,
        timestamp: Date.now(),
      },
    ]);
  };

  const handleSelectSlash = (cmd: SlashCommand) => {
    setShowSlashMenu(false);
    handleSend(cmd.command);
  };

  const activeApprovals = approvals.filter(
    (a) => a.queue_state === "active" && a.status === "pending"
  );
  const queuedApprovals = approvals.filter((a) => a.queue_state === "queued");

  return (
    <div style={{ display: "flex", height: "100vh", width: "100vw", background: "var(--ryu-canvas)" }}>
      {/* ─── 1. LEFT COLLAPSIBLE SIDEBAR ─────────────────────────────── */}
      <aside
        style={{
          width: sidebarCollapsed ? "56px" : "260px",
          background: "var(--ryu-card)",
          borderRight: "1px solid var(--ryu-border)",
          display: "flex",
          flexDirection: "column",
          transition: "width 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
          overflow: "hidden",
          flexShrink: 0,
          zIndex: 20,
        }}
      >
        {/* Brand Header */}
        <div
          style={{
            height: "52px",
            display: "flex",
            alignItems: "center",
            justifyContent: sidebarCollapsed ? "center" : "space-between",
            padding: sidebarCollapsed ? "0" : "0 14px",
            borderBottom: "1px solid var(--ryu-border)",
          }}
        >
          {!sidebarCollapsed && (
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span
                style={{
                  fontWeight: 800,
                  fontSize: "15px",
                  letterSpacing: "1px",
                  color: "var(--ryu-gold-500)",
                }}
              >
                RYU AI
              </span>
              <span
                style={{
                  fontSize: "10px",
                  fontWeight: 600,
                  padding: "1px 5px",
                  borderRadius: "4px",
                  background: "var(--ryu-card-hover)",
                  color: "var(--ryu-text-400)",
                  border: "1px solid var(--ryu-border)",
                }}
              >
                Phase 8.5
              </span>
            </div>
          )}

          <button
            onClick={() => setSidebarCollapsed((prev) => !prev)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--ryu-text-400)",
              cursor: "pointer",
              padding: "6px",
              borderRadius: "4px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        {/* New Prompt Button */}
        <div style={{ padding: "10px 12px" }}>
          <button
            onClick={() => {
              setMessages([]);
              inputRef.current?.focus();
            }}
            style={{
              width: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: sidebarCollapsed ? "center" : "flex-start",
              gap: "8px",
              background: "var(--ryu-card-hover)",
              border: "1px solid var(--ryu-border)",
              color: "var(--ryu-text-100)",
              padding: "8px 12px",
              borderRadius: "6px",
              fontSize: "12px",
              fontWeight: 500,
              cursor: "pointer",
            }}
            title="New Instruction"
          >
            <Plus size={15} color="var(--ryu-gold-500)" />
            {!sidebarCollapsed && <span>New Instruction</span>}
          </button>
        </div>

        {/* Spaces Section */}
        <div style={{ flex: 1, overflowY: "auto", padding: "0 10px" }}>
          {!sidebarCollapsed && (
            <div
              style={{
                fontSize: "11px",
                fontWeight: 600,
                color: "var(--ryu-text-600)",
                padding: "8px 6px 4px 6px",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                display: "flex",
                alignItems: "center",
                gap: "6px",
              }}
            >
              <Compass size={12} />
              <span>Isolated Spaces</span>
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "4px" }}>
            {spaces.map((sp) => {
              const isActive = sp.space_id === currentSpaceId;
              return (
                <button
                  key={sp.space_id}
                  onClick={() => setCurrentSpaceId(sp.space_id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    width: "100%",
                    padding: sidebarCollapsed ? "8px 0" : "7px 10px",
                    justifyContent: sidebarCollapsed ? "center" : "flex-start",
                    background: isActive ? "var(--ryu-card-hover)" : "transparent",
                    border: `1px solid ${isActive ? "var(--ryu-border-subtle)" : "transparent"}`,
                    borderRadius: "6px",
                    color: isActive ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                    fontSize: "12px",
                    fontWeight: isActive ? 600 : 400,
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                  title={`Space: ${sp.name} (${sp.space_id})`}
                >
                  <span
                    style={{
                      width: "7px",
                      height: "7px",
                      borderRadius: "50%",
                      backgroundColor: isActive ? "var(--ryu-emerald-500)" : "var(--ryu-text-600)",
                      flexShrink: 0,
                    }}
                  />
                  {!sidebarCollapsed && (
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {sp.name}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Footer: Connection Status & Token */}
        <div
          style={{
            padding: "10px",
            borderTop: "1px solid var(--ryu-border)",
            background: "var(--ryu-card-subtle)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: sidebarCollapsed ? "center" : "space-between",
              gap: "6px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span
                style={{
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  backgroundColor: isOnline ? "var(--ryu-emerald-500)" : "var(--ryu-red-500)",
                }}
              />
              {!sidebarCollapsed && (
                <div style={{ display: "flex", flexDirection: "column" }}>
                  <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
                    {isOnline ? "Daemon Loopback" : "Daemon Offline"}
                  </span>
                  {isOnline && latencyMs !== null && (
                    <span style={{ fontSize: "10px", color: "var(--ryu-emerald-500)" }}>
                      Online • {latencyMs}ms
                    </span>
                  )}
                </div>
              )}
            </div>

            {!sidebarCollapsed && (
              <button
                onClick={() => setShowSettings(true)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--ryu-text-400)",
                  cursor: "pointer",
                  padding: "4px",
                  borderRadius: "4px",
                }}
                title="Bearer Token Settings"
              >
                <Key size={14} />
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* ─── 2. CENTER CONVERSATIONAL CANVAS ─────────────────────────── */}
      <main
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          height: "100%",
          position: "relative",
          overflow: "hidden",
          background: "var(--ryu-canvas)",
        }}
      >
        {/* Top Space Bar */}
        <header
          style={{
            height: "52px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 20px",
            borderBottom: "1px solid var(--ryu-border)",
            background: "var(--ryu-canvas)",
            zIndex: 10,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ fontSize: "11px", color: "var(--ryu-text-600)" }}>Space:</span>
              <span style={{ fontWeight: 600, fontSize: "13px", color: "var(--ryu-text-100)" }}>
                {currentSpaceId}
              </span>
            </div>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "5px",
                padding: "2px 8px",
                borderRadius: "12px",
                background: "rgba(16, 185, 129, 0.08)",
                border: "1px solid rgba(16, 185, 129, 0.2)",
                fontSize: "11px",
                color: "var(--ryu-emerald-500)",
              }}
            >
              <Shield size={12} />
              <span>UNTAINTED (TTY)</span>
            </div>

            {attention && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "5px",
                  padding: "2px 8px",
                  borderRadius: "12px",
                  background: attention.is_saturated ? "rgba(245, 158, 11, 0.1)" : "rgba(212, 175, 55, 0.1)",
                  border: `1px solid ${attention.is_saturated ? "rgba(245, 158, 11, 0.3)" : "rgba(212, 175, 55, 0.25)"}`,
                  fontSize: "11px",
                  color: attention.is_saturated ? "var(--ryu-amber-500)" : "var(--ryu-gold-500)",
                }}
              >
                <Activity size={12} />
                <span>
                  Attention: {attention.active_count} / {attention.concurrency_limit} active gates
                </span>
              </div>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {/* Live LLM On/Off Switch */}
            <button
              onClick={toggleLiveLLM}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                padding: "4px 10px",
                borderRadius: "14px",
                background: liveLLMEnabled ? "rgba(16, 185, 129, 0.15)" : "rgba(255, 255, 255, 0.05)",
                border: `1px solid ${liveLLMEnabled ? "var(--ryu-emerald-500)" : "var(--ryu-border)"}`,
                color: liveLLMEnabled ? "var(--ryu-emerald-400)" : "var(--ryu-text-400)",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
                transition: "all 0.2s ease",
              }}
              title={
                liveLLMEnabled
                  ? `Live LLM is ACTIVE (${llmModel} via ${llmProvider}). Click to switch to Deterministic mode.`
                  : "Deterministic Fast Mode is ACTIVE. Click to switch to Live LLM (Ollama/OpenAI)."
              }
            >
              <Sparkles size={12} color={liveLLMEnabled ? "var(--ryu-emerald-400)" : "var(--ryu-text-400)"} />
              <span>{liveLLMEnabled ? `LLM: ON (${llmModel})` : "LLM: OFF (Deterministic)"}</span>
            </button>

            <button
              onClick={() => setIsPaletteOpen(true)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "5px",
                padding: "5px 10px",
                background: "var(--ryu-card)",
                border: "1px solid var(--ryu-border)",
                borderRadius: "6px",
                color: "var(--ryu-text-400)",
                fontSize: "11px",
                cursor: "pointer",
              }}
            >
              <Terminal size={13} />
              <span>Palette</span>
              <kbd style={{ fontSize: "10px", color: "var(--ryu-text-600)", marginLeft: "4px" }}>Ctrl+K</kbd>
            </button>

            <button
              onClick={() => setDrawerOpen((prev) => !prev)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                padding: "5px 10px",
                background: drawerOpen ? "var(--ryu-card-hover)" : "var(--ryu-card)",
                border: "1px solid var(--ryu-border)",
                borderRadius: "6px",
                color: drawerOpen ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                fontSize: "11px",
                fontWeight: 500,
                cursor: "pointer",
              }}
              title="Toggle Governance Drawer"
            >
              {drawerOpen ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
              <span>Governance ({activeApprovals.length})</span>
            </button>
          </div>
        </header>

        {/* Message Stream */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "24px 20px",
            display: "flex",
            flexDirection: "column",
            gap: "20px",
          }}
        >
          {messages.length === 0 ? (
            /* Welcome / Starter View */
            <div
              style={{
                margin: "auto",
                maxWidth: "600px",
                textAlign: "center",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "16px",
                padding: "20px",
              }}
            >
              <div
                style={{
                  width: "48px",
                  height: "48px",
                  borderRadius: "12px",
                  background: "rgba(212, 175, 55, 0.12)",
                  border: "1px solid rgba(212, 175, 55, 0.25)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <Sparkles size={24} color="var(--ryu-gold-500)" />
              </div>

              <div>
                <h2 style={{ fontSize: "18px", fontWeight: 700, color: "var(--ryu-text-100)", marginBottom: "6px" }}>
                  RYU Cognitive Developer Console
                </h2>
                <p style={{ fontSize: "13px", color: "var(--ryu-text-400)", lineHeight: 1.5 }}>
                  Ask natural language programming goals or run SCCA governance slash commands.
                  Simple instructions execute via the single-agent fast path (§18).
                </p>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "10px",
                  width: "100%",
                  marginTop: "8px",
                }}
              >
                {[
                  {
                    title: "write basic python program",
                    desc: "Single-agent fast-path test (§18)",
                    action: "write basic python program",
                  },
                  {
                    title: "/status",
                    desc: "Check dynamic attention budget & gates",
                    action: "/status",
                  },
                  {
                    title: "/approvals",
                    desc: "Inspect pending capability gates",
                    action: "/approvals",
                  },
                  {
                    title: "/stream",
                    desc: "Real-time pulse event timeline",
                    action: "/stream",
                  },
                ].map((item, idx) => (
                  <button
                    key={idx}
                    onClick={() => handleSend(item.action)}
                    style={{
                      background: "var(--ryu-card)",
                      border: "1px solid var(--ryu-border)",
                      borderRadius: "8px",
                      padding: "12px",
                      textAlign: "left",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      gap: "4px",
                      transition: "border-color 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--ryu-border-subtle)")}
                    onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--ryu-border)")}
                  >
                    <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
                      {item.title}
                    </span>
                    <span style={{ fontSize: "11px", color: "var(--ryu-text-600)" }}>
                      {item.desc}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => {
              if (msg.role === "user") {
                return (
                  <div
                    key={msg.id}
                    style={{
                      display: "flex",
                      justifyContent: "flex-end",
                      gap: "10px",
                      maxWidth: "800px",
                      alignSelf: "flex-end",
                      width: "100%",
                    }}
                  >
                    <div
                      style={{
                        background: "var(--ryu-card)",
                        border: "1px solid var(--ryu-border-subtle)",
                        borderRadius: "12px",
                        borderTopRightRadius: "4px",
                        padding: "10px 14px",
                        color: "var(--ryu-text-100)",
                        fontSize: "13px",
                      }}
                    >
                      {msg.content}
                    </div>
                    <div
                      style={{
                        width: "30px",
                        height: "30px",
                        borderRadius: "50%",
                        background: "var(--ryu-card-hover)",
                        border: "1px solid var(--ryu-border)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <User size={16} color="var(--ryu-text-400)" />
                    </div>
                  </div>
                );
              }

              if (msg.role === "system") {
                return (
                  <div
                    key={msg.id}
                    style={{
                      background: "rgba(24, 24, 27, 0.6)",
                      border: "1px dashed var(--ryu-border)",
                      borderRadius: "8px",
                      padding: "12px 16px",
                      maxWidth: "800px",
                      margin: "0 auto",
                      width: "100%",
                    }}
                  >
                    <MarkdownMessage content={msg.content} />
                  </div>
                );
              }

              // Assistant message
              return (
                <div
                  key={msg.id}
                  style={{
                    display: "flex",
                    gap: "12px",
                    maxWidth: "850px",
                    width: "100%",
                    alignSelf: "flex-start",
                  }}
                >
                  <div
                    style={{
                      width: "32px",
                      height: "32px",
                      borderRadius: "8px",
                      background: "rgba(212, 175, 55, 0.15)",
                      border: "1px solid rgba(212, 175, 55, 0.3)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                      marginTop: "2px",
                    }}
                  >
                    <Bot size={18} color="var(--ryu-gold-500)" />
                  </div>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {/* SCCA Execution Badge */}
                    {msg.goalId && (
                      <div
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "6px",
                          padding: "2px 8px",
                          borderRadius: "4px",
                          background: "var(--ryu-card)",
                          border: "1px solid var(--ryu-border)",
                          fontSize: "11px",
                          color: "var(--ryu-text-400)",
                          marginBottom: "8px",
                        }}
                      >
                        <Zap size={11} color="var(--ryu-gold-500)" />
                        <span style={{ fontWeight: 600, color: "var(--ryu-gold-400)" }}>
                          {msg.singleAgentEligible ? "Single-Agent Fast Path (SCCA §18)" : "Multi-Agent DAG"}
                        </span>
                        <span style={{ color: "var(--ryu-text-600)" }}>•</span>
                        <span>{msg.goalId}</span>
                      </div>
                    )}

                    {msg.status === "loading" ? (
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--ryu-text-400)" }}>
                        <RefreshCw size={14} className="animate-spin" />
                        <span>Evaluating goal and synthesizing response...</span>
                      </div>
                    ) : (
                      <MarkdownMessage content={msg.content} />
                    )}
                  </div>
                </div>
              );
            })
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Floating Input Pill */}
        <div
          style={{
            padding: "16px 20px",
            background: "linear-gradient(to top, var(--ryu-canvas) 85%, transparent)",
            position: "relative",
          }}
        >
          <div
            style={{
              position: "relative",
              maxWidth: "800px",
              margin: "0 auto",
            }}
          >
            {/* Slash Command Autocomplete Menu */}
            {showSlashMenu && (
              <SlashCommandMenu
                filter={slashFilter}
                onSelect={handleSelectSlash}
                onClose={() => setShowSlashMenu(false)}
              />
            )}

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                background: "var(--ryu-card)",
                border: "1px solid var(--ryu-border-subtle)",
                borderRadius: "12px",
                padding: "8px 12px",
                boxShadow: "0 4px 20px rgba(0, 0, 0, 0.3)",
              }}
            >
              <button
                onClick={() => setShowSlashMenu((prev) => !prev)}
                style={{
                  background: "var(--ryu-card-hover)",
                  border: "1px solid var(--ryu-border)",
                  borderRadius: "6px",
                  color: "var(--ryu-gold-500)",
                  fontWeight: 700,
                  fontSize: "13px",
                  padding: "4px 8px",
                  cursor: "pointer",
                }}
                title="Slash commands"
              >
                /
              </button>

              <textarea
                ref={inputRef}
                rows={1}
                value={inputPrompt}
                onChange={handleInputChange}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="Ask RYU, write code, or type '/' for commands..."
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  color: "var(--ryu-text-100)",
                  fontSize: "13px",
                  resize: "none",
                  fontFamily: "inherit",
                }}
              />

              <button
                onClick={() => handleSend()}
                disabled={!inputPrompt.trim() || isSubmitting}
                style={{
                  width: "32px",
                  height: "32px",
                  borderRadius: "8px",
                  background: inputPrompt.trim() ? "var(--ryu-gold-500)" : "var(--ryu-card-hover)",
                  border: "none",
                  color: inputPrompt.trim() ? "var(--ryu-canvas)" : "var(--ryu-text-600)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: inputPrompt.trim() ? "pointer" : "default",
                  transition: "all 0.15s ease",
                }}
              >
                <ArrowUp size={16} strokeWidth={2.5} />
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* ─── 3. RIGHT GOVERNANCE DRAWER ──────────────────────────────── */}
      {drawerOpen && (
        <aside
          style={{
            width: "360px",
            background: "var(--ryu-card)",
            borderLeft: "1px solid var(--ryu-border)",
            display: "flex",
            flexDirection: "column",
            flexShrink: 0,
            zIndex: 20,
          }}
        >
          {/* Drawer Header Tabs */}
          <div
            style={{
              height: "52px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0 12px",
              borderBottom: "1px solid var(--ryu-border)",
            }}
          >
            <div style={{ display: "flex", gap: "4px" }}>
              <button
                onClick={() => setDrawerTab("attention")}
                style={{
                  background: drawerTab === "attention" ? "var(--ryu-card-hover)" : "transparent",
                  color: drawerTab === "attention" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                  border: "none",
                  padding: "4px 8px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Gates ({activeApprovals.length})
              </button>
              <button
                onClick={() => setDrawerTab("stream")}
                style={{
                  background: drawerTab === "stream" ? "var(--ryu-card-hover)" : "transparent",
                  color: drawerTab === "stream" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                  border: "none",
                  padding: "4px 8px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Pulses ({streamPulses.length})
              </button>
              <button
                onClick={() => setDrawerTab("tasks")}
                style={{
                  background: drawerTab === "tasks" ? "var(--ryu-card-hover)" : "transparent",
                  color: drawerTab === "tasks" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                  border: "none",
                  padding: "4px 8px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Tasks ({tasks.length})
              </button>
              <button
                onClick={() => setDrawerTab("audit")}
                style={{
                  background: drawerTab === "audit" ? "var(--ryu-card-hover)" : "transparent",
                  color: drawerTab === "audit" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                  border: "none",
                  padding: "4px 8px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Audit ({auditEvents.length})
              </button>
            </div>

            <button
              onClick={() => setDrawerOpen(false)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--ryu-text-400)",
                cursor: "pointer",
                padding: "4px",
              }}
            >
              <PanelRightClose size={15} />
            </button>
          </div>

          {/* Drawer Body */}
          <div style={{ flex: 1, overflowY: "auto", padding: "14px" }}>
            {drawerTab === "attention" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                {/* Dynamic N Gauge */}
                <AttentionGauge attention={attention} />

                {/* Active Approvals */}
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
                      Active Gates ({activeApprovals.length})
                    </span>
                    <button
                      onClick={refreshState}
                      style={{ background: "transparent", border: "none", color: "var(--ryu-text-400)", cursor: "pointer" }}
                      title="Refresh"
                    >
                      <RefreshCw size={12} />
                    </button>
                  </div>

                  {activeApprovals.length === 0 ? (
                    <div
                      style={{
                        padding: "16px",
                        background: "var(--ryu-canvas)",
                        border: "1px dashed var(--ryu-border)",
                        borderRadius: "6px",
                        textAlign: "center",
                        color: "var(--ryu-text-600)",
                        fontSize: "11px",
                      }}
                    >
                      No pending human capability gates.
                    </div>
                  ) : (
                    activeApprovals.map((app) => (
                      <ApprovalCard
                        key={app.approval_id || app.request_id}
                        approval={app}
                        onResolved={refreshState}
                      />
                    ))
                  )}

                  {queuedApprovals.length > 0 && (
                    <div style={{ marginTop: "10px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-amber-500)" }}>
                        Queued Approvals ({queuedApprovals.length}) — Attention Cap Held
                      </span>
                      {queuedApprovals.map((app) => (
                        <div
                          key={app.approval_id || app.request_id}
                          style={{
                            padding: "8px 10px",
                            background: "var(--ryu-canvas)",
                            border: "1px solid rgba(245, 158, 11, 0.2)",
                            borderRadius: "4px",
                            marginTop: "6px",
                            fontSize: "11px",
                            display: "flex",
                            justifyContent: "space-between",
                          }}
                        >
                          <span>{app.capability || app.capability_name}</span>
                          <span style={{ color: "var(--ryu-amber-500)", fontWeight: 600 }}>QUEUED</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {drawerTab === "stream" && (
              <PulseTimeline pulses={streamPulses} />
            )}

            {drawerTab === "tasks" && (
              <TaskListView tasks={tasks} />
            )}

            {drawerTab === "audit" && (
              <AuditView events={auditEvents} onRefresh={refreshState} />
            )}
          </div>
        </aside>
      )}

      {/* ─── 4. COMMAND PALETTE MODAL (Ctrl+K) ───────────────────────── */}
      <CommandPalette
        isOpen={isPaletteOpen}
        onClose={() => setIsPaletteOpen(false)}
        onExecute={(action) => {
          if (action === "refresh") refreshState();
          else if (action === "audit") {
            setDrawerOpen(true);
            setDrawerTab("attention");
          } else if (action === "tasks") {
            setDrawerOpen(true);
            setDrawerTab("tasks");
          } else if (action === "timeline") {
            setDrawerOpen(true);
            setDrawerTab("stream");
          }
        }}
      />

      {/* ─── 5. DAEMON TOKEN MODAL ───────────────────────────────────── */}
      {showSettings && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(9, 9, 11, 0.8)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={() => setShowSettings(false)}
        >
          <div
            style={{
              width: "420px",
              background: "var(--ryu-card)",
              border: "1px solid var(--ryu-border)",
              borderRadius: "10px",
              padding: "20px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Key size={18} color="var(--ryu-gold-500)" />
              <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--ryu-text-100)" }}>
                Local Channel Daemon Token
              </span>
            </div>

            <p style={{ fontSize: "12px", color: "var(--ryu-text-400)", lineHeight: 1.5 }}>
              Enter the bearer token required to connect to the local loopback adapter.
              Raw approver signing keys are never persisted in the daemon.
            </p>

            <input
              type="text"
              placeholder="Bearer Token (32-byte hex)"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              style={{
                background: "var(--ryu-canvas)",
                border: "1px solid var(--ryu-border)",
                borderRadius: "6px",
                padding: "8px 12px",
                color: "var(--ryu-text-100)",
                fontSize: "12px",
                fontFamily: "var(--font-mono)",
                outline: "none",
              }}
            />

            {/* Divider */}
            <div style={{ height: "1px", background: "var(--ryu-border)", margin: "4px 0" }} />

            {/* LLM Generation Section */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Sparkles size={16} color="var(--ryu-emerald-400)" />
                <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--ryu-text-100)" }}>
                  Real LLM Generation
                </span>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", fontSize: "12px", color: liveLLMEnabled ? "var(--ryu-emerald-400)" : "var(--ryu-text-400)" }}>
                <input
                  type="checkbox"
                  checked={liveLLMEnabled}
                  onChange={(e) => setLiveLLMEnabled(e.target.checked)}
                  style={{ cursor: "pointer" }}
                />
                <span>{liveLLMEnabled ? "Active" : "Disabled"}</span>
              </label>
            </div>

            <p style={{ fontSize: "11px", color: "var(--ryu-text-400)", lineHeight: 1.4 }}>
              When enabled, prompts query your real local LLM (Ollama) or custom cloud model instead of fast deterministic templates.
            </p>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
              <div>
                <label style={{ fontSize: "11px", color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
                  Provider:
                </label>
                <select
                  value={llmProvider}
                  onChange={(e) => setLlmProvider(e.target.value)}
                  style={{
                    width: "100%",
                    background: "var(--ryu-canvas)",
                    border: "1px solid var(--ryu-border)",
                    borderRadius: "6px",
                    padding: "6px 8px",
                    color: "var(--ryu-text-100)",
                    fontSize: "12px",
                  }}
                >
                  <option value="ollama">Ollama (Local)</option>
                  <option value="openai">OpenAI / Compatible</option>
                </select>
              </div>

              <div>
                <label style={{ fontSize: "11px", color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
                  Model:
                </label>
                <input
                  type="text"
                  list="ryu-suggested-models"
                  placeholder="e.g. qwen3.5:0.8b, qwen3.5:4b, gemma4:12b"
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                  style={{
                    width: "100%",
                    background: "var(--ryu-canvas)",
                    border: "1px solid var(--ryu-border)",
                    borderRadius: "6px",
                    padding: "6px 8px",
                    color: "var(--ryu-text-100)",
                    fontSize: "12px",
                    boxSizing: "border-box",
                  }}
                />
                <datalist id="ryu-suggested-models">
                  <option value="qwen3.5:0.8b" />
                  <option value="qwen3.5:4b" />
                  <option value="gemma4:12b" />
                  <option value="qwen2.5-coder:3b" />
                  <option value="qwen3.5:2b" />
                  <option value="llama3" />
                  <option value="gpt-4o-mini" />
                </datalist>
              </div>
            </div>

            <div>
              <label style={{ fontSize: "11px", color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
                Base URL:
              </label>
              <input
                type="text"
                placeholder="http://localhost:11434"
                value={llmBaseUrl}
                onChange={(e) => setLlmBaseUrl(e.target.value)}
                style={{
                  width: "100%",
                  background: "var(--ryu-canvas)",
                  border: "1px solid var(--ryu-border)",
                  borderRadius: "6px",
                  padding: "6px 8px",
                  color: "var(--ryu-text-100)",
                  fontSize: "12px",
                  boxSizing: "border-box",
                }}
              />
            </div>

            {llmProvider === "openai" && (
              <div>
                <label style={{ fontSize: "11px", color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
                  API Key:
                </label>
                <input
                  type="password"
                  placeholder="sk-..."
                  value={llmApiKey}
                  onChange={(e) => setLlmApiKey(e.target.value)}
                  style={{
                    width: "100%",
                    background: "var(--ryu-canvas)",
                    border: "1px solid var(--ryu-border)",
                    borderRadius: "6px",
                    padding: "6px 8px",
                    color: "var(--ryu-text-100)",
                    fontSize: "12px",
                    boxSizing: "border-box",
                  }}
                />
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "12px" }}>
              <button
                onClick={() => setShowSettings(false)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--ryu-border)",
                  color: "var(--ryu-text-400)",
                  padding: "6px 12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  api.setDaemonToken(tokenInput.trim());
                  localStorage.setItem("ryu_live_llm", String(liveLLMEnabled));
                  try {
                    await api.setLLMConfig({
                      enabled: liveLLMEnabled,
                      provider: llmProvider,
                      base_url: llmBaseUrl,
                      model: llmModel,
                      api_key: llmApiKey || undefined,
                    });
                  } catch (e: any) {
                    console.error("Failed to save LLM config:", e);
                  }
                  setShowSettings(false);
                  refreshState();
                }}
                style={{
                  background: "var(--ryu-gold-500)",
                  border: "none",
                  color: "var(--ryu-canvas)",
                  padding: "6px 14px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Save & Connect
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
