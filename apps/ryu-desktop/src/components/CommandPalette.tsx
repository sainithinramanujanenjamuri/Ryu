import React, { useEffect, useState } from "react";

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  onExecute: (action: string) => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({ isOpen, onClose, onExecute }) => {
  const [query, setQuery] = useState("");

  const actions = [
    { id: "refresh", label: "Refresh Runtime State & Gates", shortcut: "R" },
    { id: "audit", label: "Switch to Audit Stream View", shortcut: "A" },
    { id: "timeline", label: "Switch to Pulse Timeline View", shortcut: "T" },
    { id: "tasks", label: "Switch to Task Graph View", shortcut: "P" },
  ];

  const filtered = actions.filter((a) =>
    a.label.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    if (isOpen) {
      window.addEventListener("keydown", handleKeyDown);
    }
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: "rgba(10, 11, 14, 0.75)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "15vh",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "500px",
          background: "var(--ryu-black-800)",
          border: "1px solid var(--ryu-blue-600)",
          borderRadius: "8px",
          boxShadow: "0 12px 36px rgba(0, 0, 0, 0.6)",
          overflow: "hidden",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ padding: "12px", borderBottom: "1px solid var(--ryu-black-700)" }}>
          <input
            type="text"
            placeholder="Type a command or search..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
            style={{
              width: "100%",
              background: "var(--ryu-black-900)",
              border: "1px solid var(--ryu-black-700)",
              borderRadius: "4px",
              padding: "8px 12px",
              color: "var(--ryu-text-100)",
              fontSize: "13px",
              outline: "none",
            }}
          />
        </div>

        <div style={{ maxHeight: "260px", overflowY: "auto", padding: "6px" }}>
          {filtered.map((item) => (
            <div
              key={item.id}
              onClick={() => {
                onExecute(item.id);
                onClose();
              }}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 12px",
                borderRadius: "4px",
                cursor: "pointer",
                color: "var(--ryu-text-100)",
                fontSize: "12px",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--ryu-blue-800)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
              }}
            >
              <span>{item.label}</span>
              <span
                style={{
                  fontSize: "10px",
                  color: "var(--ryu-text-400)",
                  background: "var(--ryu-black-700)",
                  padding: "2px 6px",
                  borderRadius: "3px",
                }}
              >
                {item.shortcut}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

