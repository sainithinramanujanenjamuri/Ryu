import React, { useState } from "react";
import { PulseEvent } from "../types";

interface PulseTimelineProps {
  pulses: PulseEvent[];
}

export const PulseTimeline: React.FC<PulseTimelineProps> = ({ pulses }) => {
  const [selectedPulse, setSelectedPulse] = useState<PulseEvent | null>(null);

  const getSeverityColor = (sev: string) => {
    switch (sev.toLowerCase()) {
      case "critical":
        return "var(--ryu-red-500)";
      case "error":
        return "#f87171";
      case "warning":
        return "var(--ryu-amber-500)";
      case "info":
        return "var(--ryu-blue-600)";
      default:
        return "var(--ryu-text-600)";
    }
  };

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
        }}
      >
        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
          Real-Time Pulse Stream
        </span>
        <span style={{ fontSize: "11px", color: "var(--ryu-text-400)", fontFamily: "var(--font-mono)" }}>
          {pulses.length} events
        </span>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "8px" }}>
        {pulses.length === 0 ? (
          <div style={{ padding: "24px", textAlign: "center", color: "var(--ryu-text-600)" }}>
            Listening for live pulse events...
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {pulses.slice(-100).reverse().map((pulse) => {
              const isSelected = selectedPulse?.id === pulse.id;
              return (
                <div
                  key={pulse.id}
                  onClick={() => setSelectedPulse(isSelected ? null : pulse)}
                  style={{
                    padding: "8px 12px",
                    background: isSelected ? "var(--ryu-blue-900)" : "var(--ryu-black-900)",
                    border: `1px solid ${isSelected ? "var(--ryu-blue-600)" : "var(--ryu-black-700)"}`,
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "12px",
                    fontFamily: "var(--font-mono)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "4px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span
                        style={{
                          fontSize: "9px",
                          fontWeight: 700,
                          padding: "1px 4px",
                          borderRadius: "3px",
                          color: getSeverityColor(pulse.severity),
                          background: `${getSeverityColor(pulse.severity)}22`,
                          border: `1px solid ${getSeverityColor(pulse.severity)}44`,
                        }}
                      >
                        {pulse.severity.toUpperCase()}
                      </span>
                      <span style={{ color: "var(--ryu-text-100)", fontWeight: 500 }}>
                        {pulse.type}
                      </span>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      {pulse.taint && (
                        <span style={{ fontSize: "9px", color: "var(--ryu-red-500)", fontWeight: 700 }}>
                          TAINTED
                        </span>
                      )}
                      <span style={{ fontSize: "10px", color: "var(--ryu-text-600)" }}>
                        {pulse.timestamp.slice(11, 19)}
                      </span>
                    </div>
                  </div>

                  {isSelected && pulse.payload && (
                    <pre
                      style={{
                        marginTop: "6px",
                        padding: "8px",
                        background: "var(--ryu-black-800)",
                        borderRadius: "3px",
                        fontSize: "10px",
                        color: "var(--ryu-text-400)",
                        overflowX: "auto",
                      }}
                    >
                      {JSON.stringify(pulse.payload, null, 2)}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

