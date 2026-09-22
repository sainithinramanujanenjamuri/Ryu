import React, { useEffect, useState } from "react";
import {
  Activity,
  CheckSquare,
  Compass,
  FileText,
  HelpCircle,
  Radio,
  Trash2,
} from "lucide-react";

export interface SlashCommand {
  command: string;
  label: string;
  description: string;
  icon: React.ReactNode;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    command: "/status",
    label: "Status",
    description: "Inspect runtime health and dynamic attention budget",
    icon: <Activity size={15} color="var(--ryu-emerald-500)" />,
  },
  {
    command: "/approvals",
    label: "Approvals",
    description: "Review pending capability gates and sign decisions",
    icon: <CheckSquare size={15} color="var(--ryu-gold-500)" />,
  },
  {
    command: "/spaces",
    label: "Spaces",
    description: "List isolated spaces or inspect current space",
    icon: <Compass size={15} color="var(--ryu-blue-500)" />,
  },
  {
    command: "/tasks",
    label: "Tasks",
    description: "View execution plan DAG and assigned workers",
    icon: <FileText size={15} color="var(--ryu-text-400)" />,
  },
  {
    command: "/stream",
    label: "Pulse Stream",
    description: "Open real-time immutable pulse event timeline",
    icon: <Radio size={15} color="#ec4899" />,
  },
  {
    command: "/clear",
    label: "Clear",
    description: "Clear current conversation history",
    icon: <Trash2 size={15} color="var(--ryu-amber-500)" />,
  },
  {
    command: "/help",
    label: "Help",
    description: "View SCCA architecture laws and command reference",
    icon: <HelpCircle size={15} color="var(--ryu-text-400)" />,
  },
];

interface SlashCommandMenuProps {
  filter: string;
  onSelect: (cmd: SlashCommand) => void;
  onClose: () => void;
}

export const SlashCommandMenu: React.FC<SlashCommandMenuProps> = ({
  filter,
  onSelect,
  onClose,
}) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  const cleanFilter = filter.replace("/", "").toLowerCase();
  const filteredCommands = SLASH_COMMANDS.filter(
    (c) =>
      c.command.toLowerCase().includes(cleanFilter) ||
      c.label.toLowerCase().includes(cleanFilter) ||
      c.description.toLowerCase().includes(cleanFilter)
  );

  useEffect(() => {
    setSelectedIndex(0);
  }, [filter]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % Math.max(1, filteredCommands.length));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % Math.max(1, filteredCommands.length));
      } else if (e.key === "Enter") {
        if (filteredCommands[selectedIndex]) {
          e.preventDefault();
          onSelect(filteredCommands[selectedIndex]);
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filteredCommands, selectedIndex, onSelect, onClose]);

  if (filteredCommands.length === 0) {
    return null;
  }

  return (
    <div
      style={{
        position: "absolute",
        bottom: "calc(100% + 8px)",
        left: "0",
        right: "0",
        maxHeight: "260px",
        overflowY: "auto",
        background: "var(--ryu-card)",
        border: "1px solid var(--ryu-border)",
        borderRadius: "8px",
        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)",
        zIndex: 50,
        padding: "6px",
      }}
    >
      <div
        style={{
          padding: "4px 8px 6px 8px",
          fontSize: "11px",
          fontWeight: 600,
          color: "var(--ryu-text-600)",
          borderBottom: "1px solid var(--ryu-border)",
          marginBottom: "4px",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>SLASH COMMANDS</span>
        <span style={{ fontSize: "10px" }}>Use ↑↓ and Enter</span>
      </div>

      {filteredCommands.map((cmd, idx) => {
        const isSelected = idx === selectedIndex;
        return (
          <div
            key={cmd.command}
            onClick={() => onSelect(cmd)}
            onMouseEnter={() => setSelectedIndex(idx)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "8px 10px",
              borderRadius: "6px",
              cursor: "pointer",
              background: isSelected ? "var(--ryu-card-hover)" : "transparent",
              transition: "background 0.1s ease",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: "26px",
                height: "26px",
                borderRadius: "5px",
                background: "var(--ryu-canvas)",
                border: "1px solid var(--ryu-border)",
              }}
            >
              {cmd.icon}
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ fontWeight: 600, fontSize: "12px", color: "var(--ryu-text-100)" }}>
                  {cmd.command}
                </span>
                <span style={{ fontSize: "11px", color: "var(--ryu-text-400)" }}>
                  ({cmd.label})
                </span>
              </div>
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--ryu-text-600)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {cmd.description}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

