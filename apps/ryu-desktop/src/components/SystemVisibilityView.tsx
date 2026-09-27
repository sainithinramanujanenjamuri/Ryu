import React, { useEffect, useState } from "react";
import {
  Brain,
  Cpu,
  Globe,
  RefreshCw,
  Server,
  Shield,
  Sparkles,
} from "lucide-react";
import { api } from "../api/client";
import { MemoryStateItem, NodeInfoItem } from "../types";

interface SystemVisibilityViewProps {
  spaceId: string;
}

export const SystemVisibilityView: React.FC<SystemVisibilityViewProps> = ({ spaceId }) => {
  const [subTab, setSubTab] = useState<"nodes" | "memory">("nodes");
  const [nodes, setNodes] = useState<NodeInfoItem[]>([]);
  const [memory, setMemory] = useState<MemoryStateItem | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchSystemData = async () => {
    setLoading(true);
    try {
      const [nodeList, memData] = await Promise.all([
        api.listNodes().catch(() => []),
        api.getMemory(spaceId).catch(() => null),
      ]);
      setNodes(nodeList);
      setMemory(memData);
    } catch (e) {
      console.error("Failed to load system visibility data:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSystemData();
  }, [spaceId]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "10px" }}>
      {/* Header with Sub-tabs */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", gap: "4px", background: "var(--ryu-canvas)", padding: "2px", borderRadius: "6px", border: "1px solid var(--ryu-border)" }}>
          <button
            onClick={() => setSubTab("nodes")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "4px",
              background: subTab === "nodes" ? "var(--ryu-card-hover)" : "transparent",
              color: subTab === "nodes" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
              border: "none",
              borderRadius: "4px",
              padding: "3px 8px",
              fontSize: "11px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            <Server size={12} /> Nodes ({nodes.length})
          </button>
          <button
            onClick={() => setSubTab("memory")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "4px",
              background: subTab === "memory" ? "var(--ryu-card-hover)" : "transparent",
              color: subTab === "memory" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
              border: "none",
              borderRadius: "4px",
              padding: "3px 8px",
              fontSize: "11px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            <Brain size={12} /> Memory ({memory?.experience_count || 0})
          </button>
        </div>

        <button
          onClick={fetchSystemData}
          disabled={loading}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--ryu-text-400)",
            cursor: "pointer",
            padding: "4px",
          }}
          title="Refresh system state"
        >
          <RefreshCw size={13} className={loading ? "spin" : ""} />
        </button>
      </div>

      {/* Nodes Sub-Tab */}
      {subTab === "nodes" && (
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
          <div
            style={{
              padding: "6px 10px",
              background: "rgba(212, 175, 55, 0.08)",
              border: "1px solid rgba(212, 175, 55, 0.2)",
              borderRadius: "6px",
              fontSize: "10px",
              color: "var(--ryu-gold-400)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <Shield size={12} />
            <span>Hardware leases & node grants adhere to SCCA §9</span>
          </div>

          {nodes.length === 0 ? (
            <div
              style={{
                padding: "24px 12px",
                textAlign: "center",
                color: "var(--ryu-text-600)",
                fontSize: "11px",
                border: "1px dashed var(--ryu-border)",
                borderRadius: "6px",
              }}
            >
              No remote execution nodes connected. Local kernel running in embedded loopback mode.
            </div>
          ) : (
            nodes.map((node) => (
              <div
                key={node.node_id}
                style={{
                  background: "var(--ryu-card)",
                  border: "1px solid var(--ryu-border)",
                  borderRadius: "8px",
                  padding: "10px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "6px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <Server size={14} color="var(--ryu-emerald-400)" />
                    <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
                      {node.node_id}
                    </span>
                  </div>
                  <span
                    style={{
                      fontSize: "9px",
                      textTransform: "uppercase",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      background: "rgba(16, 185, 129, 0.1)",
                      color: "var(--ryu-emerald-400)",
                      fontWeight: 600,
                    }}
                  >
                    {node.runtime_state}
                  </span>
                </div>

                <div style={{ display: "flex", gap: "10px", fontSize: "10px", color: "var(--ryu-text-400)" }}>
                  <span>Platform: {node.platform}</span>
                  <span>Trust: {node.trust_tier}</span>
                  <span>Devices: {node.device_count}</span>
                </div>

                {node.devices && node.devices.length > 0 && (
                  <div style={{ marginTop: "4px", display: "flex", flexDirection: "column", gap: "4px" }}>
                    <span style={{ fontSize: "10px", fontWeight: 600, color: "var(--ryu-text-400)" }}>
                      Attached Hardware Devices:
                    </span>
                    {node.devices.map((dev) => (
                      <div
                        key={dev.device_id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "4px 8px",
                          background: "var(--ryu-canvas)",
                          borderRadius: "4px",
                          fontSize: "10px",
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                          <Cpu size={12} color="var(--ryu-gold-400)" />
                          <span style={{ color: "var(--ryu-text-200)" }}>{dev.device_id}</span>
                          <span style={{ color: "var(--ryu-text-600)" }}>({dev.device_type})</span>
                        </div>
                        <span style={{ color: "var(--ryu-emerald-400)" }}>{dev.state}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Memory Sub-Tab */}
      {subTab === "memory" && (
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "10px" }}>
          {/* Law 4 Notice */}
          <div
            style={{
              padding: "6px 10px",
              background: "rgba(16, 185, 129, 0.08)",
              border: "1px solid rgba(16, 185, 129, 0.2)",
              borderRadius: "6px",
              fontSize: "10px",
              color: "var(--ryu-emerald-400)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <Brain size={12} />
            <span>SCCA Law 4: Knowledge Belongs to the Space First</span>
          </div>

          {/* Space Experiences */}
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-200)", display: "flex", alignItems: "center", gap: "4px" }}>
              <Sparkles size={12} color="var(--ryu-gold-400)" />
              Space-Local Experiences ({memory?.experiences.length || 0})
            </span>

            {(!memory?.experiences || memory.experiences.length === 0) ? (
              <div
                style={{
                  padding: "16px",
                  textAlign: "center",
                  color: "var(--ryu-text-600)",
                  fontSize: "11px",
                  border: "1px dashed var(--ryu-border)",
                  borderRadius: "6px",
                }}
              >
                No local space reflections or counterfactual memories recorded yet.
              </div>
            ) : (
              memory.experiences.map((exp) => (
                <div
                  key={exp.experience_id}
                  style={{
                    background: "var(--ryu-card)",
                    border: "1px solid var(--ryu-border)",
                    borderRadius: "6px",
                    padding: "8px 10px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "4px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
                      {exp.experience_id}
                    </span>
                    <span style={{ fontSize: "9px", color: "var(--ryu-emerald-400)" }}>{exp.outcome}</span>
                  </div>
                  {exp.counterfactual && (
                    <div style={{ fontSize: "10px", color: "var(--ryu-text-400)", fontStyle: "italic" }}>
                      "{exp.counterfactual}"
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          {/* Promoted Global Knowledge */}
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-200)", display: "flex", alignItems: "center", gap: "4px" }}>
              <Globe size={12} color="var(--ryu-emerald-400)" />
              Promoted Global Knowledge ({memory?.global_knowledge.length || 0})
            </span>

            {(!memory?.global_knowledge || memory.global_knowledge.length === 0) ? (
              <div
                style={{
                  padding: "14px",
                  textAlign: "center",
                  color: "var(--ryu-text-600)",
                  fontSize: "11px",
                  border: "1px dashed var(--ryu-border)",
                  borderRadius: "6px",
                }}
              >
                No globally promoted knowledge items found.
              </div>
            ) : (
              memory.global_knowledge.map((k) => (
                <div
                  key={k.knowledge_id}
                  style={{
                    background: "var(--ryu-card)",
                    border: "1px solid var(--ryu-border)",
                    borderRadius: "6px",
                    padding: "8px 10px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "4px",
                  }}
                >
                  <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
                    {k.topic}
                  </span>
                  <div style={{ fontSize: "10px", color: "var(--ryu-text-300)" }}>
                    {k.content}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
};
