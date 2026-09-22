import React from "react";
import { SpaceInfo } from "../types";

interface TopBarProps {
  spaces: SpaceInfo[];
  currentSpaceId: string;
  onSelectSpace: (spaceId: string) => void;
  isOnline: boolean;
  onOpenSettings: () => void;
}

export const TopBar: React.FC<TopBarProps> = ({
  spaces,
  currentSpaceId,
  onSelectSpace,
  isOnline,
  onOpenSettings,
}) => {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 16px",
        backgroundColor: "var(--ryu-black-800)",
        borderBottom: "1px solid var(--ryu-black-700)",
        height: "48px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            style={{
              fontWeight: 700,
              fontSize: "14px",
              letterSpacing: "1.5px",
              color: "var(--ryu-gold-500)",
            }}
          >
            RYU
          </span>
          <span
            style={{
              fontSize: "10px",
              color: "var(--ryu-text-400)",
              textTransform: "uppercase",
              padding: "2px 6px",
              background: "var(--ryu-black-700)",
              borderRadius: "4px",
            }}
          >
            Command Center
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <label style={{ fontSize: "11px", color: "var(--ryu-text-400)" }}>Space:</label>
          <select
            value={currentSpaceId}
            onChange={(e) => onSelectSpace(e.target.value)}
            style={{
              background: "var(--ryu-black-900)",
              color: "var(--ryu-text-100)",
              border: "1px solid var(--ryu-black-700)",
              borderRadius: "4px",
              padding: "4px 8px",
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            {spaces.map((s) => (
              <option key={s.space_id} value={s.space_id}>
                {s.name} ({s.space_id})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontSize: "11px",
            padding: "3px 8px",
            borderRadius: "4px",
            background: isOnline ? "rgba(16, 185, 129, 0.1)" : "rgba(239, 68, 68, 0.1)",
            color: isOnline ? "var(--ryu-green-500)" : "var(--ryu-red-500)",
            border: `1px solid ${isOnline ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
          }}
        >
          <span
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: isOnline ? "var(--ryu-green-500)" : "var(--ryu-red-500)",
            }}
          />
          {isOnline ? "DAEMON ONLINE" : "DAEMON OFFLINE"}
        </div>

        <button
          onClick={onOpenSettings}
          style={{
            background: "var(--ryu-black-700)",
            border: "none",
            color: "var(--ryu-text-100)",
            padding: "4px 10px",
            borderRadius: "4px",
            fontSize: "11px",
            cursor: "pointer",
          }}
        >
          Token Config
        </button>

        <span style={{ fontSize: "11px", color: "var(--ryu-text-600)" }}>Ctrl+K</span>
      </div>
    </header>
  );
};

