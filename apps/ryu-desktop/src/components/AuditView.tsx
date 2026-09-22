import React, { useState } from "react";
import { PulseEvent } from "../types";

interface AuditViewProps {
  events: PulseEvent[];
  onRefresh: () => void;
}

export const AuditView: React.FC<AuditViewProps> = ({ events, onRefresh }) => {
  const [filterType, setFilterType] = useState("");

  const filtered = filterType
    ? events.filter((e) => e.type.toLowerCase().includes(filterType.toLowerCase()))
    : events;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--ryu-black-800)",
        borderRadius: "6px",
        border: "1px solid var(--ryu-black-700)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--ryu-black-700)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "12px",
        }}
      >
        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
          Immutable Audit Stream
        </span>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <input
            type="text"
            placeholder="Filter pulse type..."
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            style={{
              background: "var(--ryu-black-900)",
              border: "1px solid var(--ryu-black-700)",
              borderRadius: "4px",
              padding: "4px 8px",
              color: "var(--ryu-text-100)",
              fontSize: "11px",
              width: "160px",
            }}
          />
          <button
            onClick={onRefresh}
            style={{
              background: "var(--ryu-black-700)",
              border: "none",
              color: "var(--ryu-text-100)",
              padding: "4px 8px",
              borderRadius: "4px",
              fontSize: "11px",
              cursor: "pointer",
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "8px" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px", fontFamily: "var(--font-mono)" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--ryu-black-700)", textAlign: "left", color: "var(--ryu-text-400)" }}>
              <th style={{ padding: "6px 8px" }}>ID</th>
              <th style={{ padding: "6px 8px" }}>TYPE</th>
              <th style={{ padding: "6px 8px" }}>SEV</th>
              <th style={{ padding: "6px 8px" }}>TIME</th>
              <th style={{ padding: "6px 8px" }}>TAINT</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => (
              <tr key={e.id} style={{ borderBottom: "1px solid var(--ryu-black-700)" }}>
                <td style={{ padding: "6px 8px", color: "var(--ryu-gold-500)" }}>{e.id.slice(0, 10)}</td>
                <td style={{ padding: "6px 8px", color: "var(--ryu-text-100)" }}>{e.type}</td>
                <td style={{ padding: "6px 8px", color: "var(--ryu-text-400)" }}>{e.severity.toUpperCase()}</td>
                <td style={{ padding: "6px 8px", color: "var(--ryu-text-600)" }}>{e.timestamp.slice(11, 19)}</td>
                <td style={{ padding: "6px 8px", color: e.taint ? "var(--ryu-red-500)" : "var(--ryu-green-500)" }}>
                  {e.taint ? "TAINTED" : "CLEAN"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

