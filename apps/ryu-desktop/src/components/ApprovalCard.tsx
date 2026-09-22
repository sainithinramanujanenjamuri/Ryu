import React, { useState } from "react";
import { ApprovalRequestData } from "../types";
import { api } from "../api/client";

interface ApprovalCardProps {
  approval: ApprovalRequestData;
  onResolved: () => void;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({ approval, onResolved }) => {
  const [showPrompt, setShowPrompt] = useState(false);
  const [decisionType, setDecisionType] = useState<"APPROVE" | "REJECT">("APPROVE");
  const [approverId, setApproverId] = useState(approval.approver_id || "human_operator");
  const [secretKey, setSecretKey] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const getTierColor = (tier: string) => {
    switch (tier.toLowerCase()) {
      case "critical":
        return "var(--ryu-red-500)";
      case "high":
        return "var(--ryu-amber-500)";
      case "standard":
        return "var(--ryu-blue-600)";
      default:
        return "var(--ryu-text-400)";
    }
  };

  const handleActionClick = (action: "APPROVE" | "REJECT") => {
    setDecisionType(action);
    setErrorMsg(null);
    setShowPrompt(true);
  };

  const handleSubmitDecision = async () => {
    if (!secretKey) {
      setErrorMsg("Approver secret key is required.");
      return;
    }
    setIsSubmitting(true);
    setErrorMsg(null);

    try {
      await api.signAndSubmitDecision(
        approverId,
        secretKey,
        approval.space_id,
        approval.approval_id || approval.request_id || "",
        decisionType,
        approval.plan_version,
        approval.capability_request_hash
      );
      setSecretKey(""); // Immediate clearance from memory
      setShowPrompt(false);
      onResolved();
    } catch (e: any) {
      setErrorMsg(e.message || "Failed to submit decision.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--ryu-black-800)",
        borderRadius: "6px",
        border: `1px solid ${getTierColor(approval.risk_tier)}`,
        padding: "16px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                textTransform: "uppercase",
                padding: "2px 6px",
                borderRadius: "4px",
                background: `${getTierColor(approval.risk_tier)}22`,
                color: getTierColor(approval.risk_tier),
                border: `1px solid ${getTierColor(approval.risk_tier)}55`,
              }}
            >
              {approval.risk_tier} RISK
            </span>
            <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
              {approval.capability || approval.capability_name}
            </span>
          </div>
          <span style={{ fontSize: "11px", color: "var(--ryu-text-400)", fontFamily: "var(--font-mono)" }}>
            ID: {approval.approval_id || approval.request_id}
          </span>
        </div>

        <div style={{ display: "flex", gap: "6px" }}>
          {approval.taint && (
            <span
              style={{
                fontSize: "10px",
                padding: "2px 6px",
                borderRadius: "3px",
                background: "rgba(239, 68, 68, 0.2)",
                color: "var(--ryu-red-500)",
                fontWeight: 600,
              }}
            >
              TAINTED
            </span>
          )}
          <span
            style={{
              fontSize: "10px",
              padding: "2px 6px",
              borderRadius: "3px",
              background: "var(--ryu-black-700)",
              color: "var(--ryu-text-400)",
              fontFamily: "var(--font-mono)",
            }}
          >
            v{approval.plan_version}
          </span>
        </div>
      </div>

      {approval.summary && (
        <p style={{ fontSize: "12px", color: "var(--ryu-text-100)", lineHeight: 1.4 }}>
          {approval.summary}
        </p>
      )}

      {/* Cryptographic Pre-image details */}
      <div
        style={{
          background: "var(--ryu-black-900)",
          padding: "8px 12px",
          borderRadius: "4px",
          fontSize: "11px",
          fontFamily: "var(--font-mono)",
          color: "var(--ryu-text-400)",
          display: "flex",
          flexDirection: "column",
          gap: "4px",
        }}
      >
        <div>Requester: <span style={{ color: "var(--ryu-text-100)" }}>{approval.requester_id || "space-orchestrator"}</span></div>
        <div>Hash: <span style={{ color: "var(--ryu-gold-500)" }}>{approval.capability_request_hash.slice(0, 24)}...</span></div>
        {approval.parameters && Object.keys(approval.parameters).length > 0 && (
          <div>Params: <span style={{ color: "var(--ryu-text-100)" }}>{JSON.stringify(approval.parameters)}</span></div>
        )}
      </div>

      {/* Decision Buttons */}
      {!showPrompt ? (
        <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
          <button
            onClick={() => handleActionClick("APPROVE")}
            style={{
              flex: 1,
              padding: "8px",
              background: "var(--ryu-gold-500)",
              color: "var(--ryu-black-900)",
              border: "none",
              borderRadius: "4px",
              fontWeight: 700,
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            Approve Gate
          </button>
          <button
            onClick={() => handleActionClick("REJECT")}
            style={{
              flex: 1,
              padding: "8px",
              background: "transparent",
              color: "var(--ryu-red-500)",
              border: "1px solid var(--ryu-red-500)",
              borderRadius: "4px",
              fontWeight: 600,
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            Reject Gate
          </button>
        </div>
      ) : (
        /* Un-echoed Secret Entry Form */
        <div
          style={{
            background: "var(--ryu-black-900)",
            padding: "12px",
            borderRadius: "4px",
            border: `1px solid ${decisionType === "APPROVE" ? "var(--ryu-gold-500)" : "var(--ryu-red-500)"}`,
            display: "flex",
            flexDirection: "column",
            gap: "8px",
          }}
        >
          <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
            Authenticate Decision ({decisionType})
          </span>

          <div style={{ display: "flex", gap: "8px" }}>
            <input
              type="text"
              placeholder="Approver ID"
              value={approverId}
              onChange={(e) => setApproverId(e.target.value)}
              style={{
                flex: 1,
                background: "var(--ryu-black-800)",
                border: "1px solid var(--ryu-black-700)",
                borderRadius: "4px",
                padding: "6px 8px",
                color: "var(--ryu-text-100)",
                fontSize: "11px",
              }}
            />
            <input
              type="password"
              placeholder="Approver Secret Key"
              value={secretKey}
              onChange={(e) => setSecretKey(e.target.value)}
              autoFocus
              style={{
                flex: 2,
                background: "var(--ryu-black-800)",
                border: "1px solid var(--ryu-black-700)",
                borderRadius: "4px",
                padding: "6px 8px",
                color: "var(--ryu-text-100)",
                fontSize: "11px",
              }}
            />
          </div>

          {errorMsg && (
            <span style={{ fontSize: "11px", color: "var(--ryu-red-500)" }}>{errorMsg}</span>
          )}

          <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
            <button
              onClick={() => {
                setShowPrompt(false);
                setSecretKey("");
              }}
              style={{
                background: "transparent",
                border: "1px solid var(--ryu-black-700)",
                color: "var(--ryu-text-400)",
                padding: "4px 10px",
                borderRadius: "4px",
                fontSize: "11px",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleSubmitDecision}
              disabled={isSubmitting}
              style={{
                background: decisionType === "APPROVE" ? "var(--ryu-gold-500)" : "var(--ryu-red-500)",
                color: decisionType === "APPROVE" ? "var(--ryu-black-900)" : "#fff",
                border: "none",
                padding: "4px 14px",
                borderRadius: "4px",
                fontWeight: 700,
                fontSize: "11px",
                cursor: "pointer",
              }}
            >
              {isSubmitting ? "Signing & Submitting..." : `Confirm ${decisionType}`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

