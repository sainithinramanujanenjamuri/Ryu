import React, { useState } from "react";
import { Plus, X } from "lucide-react";
import { api } from "../api/client";
import { SpaceInfo } from "../types";

interface CreateSpaceModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (space: SpaceInfo) => void;
}

export const CreateSpaceModal: React.FC<CreateSpaceModalProps> = ({
  isOpen,
  onClose,
  onCreated,
}) => {
  const [spaceId, setSpaceId] = useState("");
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("0.0");
  const [attentionLimit, setAttentionLimit] = useState("3");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanId = spaceId.trim();
    const cleanName = name.trim();

    if (!cleanId) {
      setError("Space ID cannot be empty");
      return;
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(cleanId)) {
      setError("Space ID may only contain letters, numbers, hyphens, and underscores");
      return;
    }

    if (!cleanName) {
      setError("Space Name cannot be empty");
      return;
    }

    setError(null);
    setSubmitting(true);

    try {
      const res = await api.createSpace(cleanId, cleanName, {
        budget: parseFloat(budget) || 0.0,
        attention_limit: parseInt(attentionLimit, 10) || 3,
      });

      if (res && res.space) {
        onCreated(res.space);
        onClose();
        setSpaceId("");
        setName("");
      } else {
        setError("Failed to create space");
      }
    } catch (err: any) {
      setError(err.message || "Failed to create space");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.75)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "420px",
          background: "var(--ryu-card)",
          border: "1px solid var(--ryu-border)",
          borderRadius: "10px",
          boxShadow: "0 10px 30px rgba(0, 0, 0, 0.5)",
          padding: "20px",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "16px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <Plus size={18} color="var(--ryu-gold-500)" />
            <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
              Create Isolated Space (SCCA §4)
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--ryu-text-400)",
              cursor: "pointer",
            }}
          >
            <X size={16} />
          </button>
        </div>

        {error && (
          <div
            style={{
              padding: "8px 12px",
              background: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              borderRadius: "6px",
              color: "var(--ryu-red-500)",
              fontSize: "12px",
              marginBottom: "14px",
            }}
          >
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <label style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
              Space ID *
            </label>
            <input
              type="text"
              value={spaceId}
              onChange={(e) => setSpaceId(e.target.value)}
              placeholder="e.g. research-v1"
              required
              style={{
                width: "100%",
                background: "var(--ryu-canvas)",
                border: "1px solid var(--ryu-border)",
                borderRadius: "6px",
                padding: "8px 10px",
                color: "var(--ryu-text-100)",
                fontSize: "13px",
                outline: "none",
              }}
            />
            <span style={{ fontSize: "10px", color: "var(--ryu-text-600)", marginTop: "2px", display: "block" }}>
              Unique alphanumeric slug. Once created, space boundaries are strict.
            </span>
          </div>

          <div>
            <label style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
              Display Name *
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Research & Experimentation"
              required
              style={{
                width: "100%",
                background: "var(--ryu-canvas)",
                border: "1px solid var(--ryu-border)",
                borderRadius: "6px",
                padding: "8px 10px",
                color: "var(--ryu-text-100)",
                fontSize: "13px",
                outline: "none",
              }}
            />
          </div>

          <div style={{ display: "flex", gap: "12px" }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
                Budget
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                style={{
                  width: "100%",
                  background: "var(--ryu-canvas)",
                  border: "1px solid var(--ryu-border)",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  color: "var(--ryu-text-100)",
                  fontSize: "13px",
                  outline: "none",
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: "11px", fontWeight: 600, color: "var(--ryu-text-400)", display: "block", marginBottom: "4px" }}>
                Attention Limit
              </label>
              <input
                type="number"
                min="1"
                max="10"
                value={attentionLimit}
                onChange={(e) => setAttentionLimit(e.target.value)}
                style={{
                  width: "100%",
                  background: "var(--ryu-canvas)",
                  border: "1px solid var(--ryu-border)",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  color: "var(--ryu-text-100)",
                  fontSize: "13px",
                  outline: "none",
                }}
              />
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "8px" }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                background: "var(--ryu-card-subtle)",
                border: "1px solid var(--ryu-border)",
                borderRadius: "6px",
                color: "var(--ryu-text-400)",
                padding: "8px 14px",
                fontSize: "12px",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              style={{
                background: "var(--ryu-gold-500)",
                border: "none",
                borderRadius: "6px",
                color: "var(--ryu-canvas)",
                padding: "8px 16px",
                fontSize: "12px",
                fontWeight: 600,
                cursor: submitting ? "not-allowed" : "pointer",
              }}
            >
              {submitting ? "Creating..." : "Create Space"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
