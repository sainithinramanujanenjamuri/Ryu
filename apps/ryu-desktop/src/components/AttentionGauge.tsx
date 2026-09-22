import React from "react";
import { AttentionState } from "../types";

interface AttentionGaugeProps {
  attention: AttentionState | null;
}

export const AttentionGauge: React.FC<AttentionGaugeProps> = ({ attention }) => {
  if (!attention) {
    return (
      <div
        style={{
          padding: "12px",
          background: "var(--ryu-black-800)",
          borderRadius: "6px",
          border: "1px solid var(--ryu-black-700)",
        }}
      >
        <span style={{ color: "var(--ryu-text-600)", fontSize: "11px" }}>Loading attention budget...</span>
      </div>
    );
  }

  const limit = attention.concurrency_limit || 3;
  const active = attention.active_count || 0;
  const queued = attention.queued_count || 0;
  const isSaturated = attention.is_saturated || active >= limit;

  // Generate visual slots for N
  const slots = Array.from({ length: limit }, (_, i) => i < active);

  return (
    <div
      style={{
        padding: "12px 16px",
        background: "var(--ryu-black-800)",
        borderRadius: "6px",
        border: `1px solid ${isSaturated ? "var(--ryu-amber-500)" : "var(--ryu-black-700)"}`,
        display: "flex",
        flexDirection: "column",
        gap: "8px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
            Human Attention Budget
          </span>
          <span
            style={{
              fontSize: "10px",
              padding: "2px 6px",
              borderRadius: "4px",
              fontWeight: 600,
              background: isSaturated ? "rgba(245, 158, 11, 0.15)" : "rgba(16, 185, 129, 0.15)",
              color: isSaturated ? "var(--ryu-amber-500)" : "var(--ryu-green-500)",
              border: `1px solid ${isSaturated ? "rgba(245, 158, 11, 0.3)" : "rgba(16, 185, 129, 0.3)"}`,
            }}
          >
            {isSaturated ? "SATURATED" : "NORMAL"}
          </span>
        </div>
        <span style={{ fontSize: "12px", fontFamily: "var(--font-mono)", color: "var(--ryu-gold-500)" }}>
          {active} / {limit} slots active {queued > 0 ? `(${queued} queued)` : ""}
        </span>
      </div>

      {/* Visual slots representation for dynamic N */}
      <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
        {slots.map((occupied, idx) => (
          <div
            key={idx}
            style={{
              flex: 1,
              height: "8px",
              borderRadius: "3px",
              background: occupied
                ? isSaturated
                  ? "var(--ryu-amber-500)"
                  : "var(--ryu-gold-500)"
                : "var(--ryu-black-700)",
              boxShadow: occupied ? "0 0 8px rgba(212, 175, 55, 0.4)" : "none",
              transition: "all 0.2s ease-in-out",
            }}
            title={occupied ? `Slot ${idx + 1}: Occupied` : `Slot ${idx + 1}: Available`}
          />
        ))}
      </div>
    </div>
  );
};

