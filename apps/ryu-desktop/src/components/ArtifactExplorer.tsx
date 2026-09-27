import React, { useEffect, useState } from "react";
import {
  Code,
  Copy,
  Download,
  Eye,
  FileCode,
  FileText,
  FolderOpen,
  RefreshCw,
  Search,
  Shield,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { ArtifactItem } from "../types";

interface ArtifactExplorerProps {
  spaceId: string;
}

export const ArtifactExplorer: React.FC<ArtifactExplorerProps> = ({ spaceId }) => {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterQuery, setFilterQuery] = useState("");
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactItem | null>(null);
  const [artifactContent, setArtifactContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);
  const [contentViewMode, setContentViewMode] = useState<"preview" | "code">("preview");
  const [copiedSha, setCopiedSha] = useState<string | null>(null);
  const [copiedContent, setCopiedContent] = useState(false);

  const fetchArtifacts = async () => {
    setLoading(true);
    try {
      const items = await api.listArtifacts(spaceId);
      setArtifacts(items);
      if (selectedArtifact && !items.find((a) => a.artifact_id === selectedArtifact.artifact_id)) {
        setSelectedArtifact(null);
        setArtifactContent(null);
      }
    } catch (e) {
      console.error("Failed to load artifacts:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchArtifacts();
  }, [spaceId]);

  const handleSelectArtifact = async (art: ArtifactItem) => {
    setSelectedArtifact(art);
    setLoadingContent(true);
    const isHtml = art.mime_type === "text/html" || art.name.endsWith(".html");
    setContentViewMode(isHtml ? "preview" : "code");
    try {
      const res = await api.getArtifactContent(spaceId, art.artifact_id);
      setArtifactContent(res.content);
    } catch (e) {
      console.error("Failed to load artifact content:", e);
      setArtifactContent("Error loading artifact content");
    } finally {
      setLoadingContent(false);
    }
  };

  const handleCopySha = (sha: string) => {
    navigator.clipboard.writeText(sha);
    setCopiedSha(sha);
    setTimeout(() => setCopiedSha(null), 2000);
  };

  const handleCopyContent = () => {
    if (artifactContent) {
      navigator.clipboard.writeText(artifactContent);
      setCopiedContent(true);
      setTimeout(() => setCopiedContent(false), 2000);
    }
  };

  const handleDownload = (art: ArtifactItem, content: string | null) => {
    if (!content) return;
    const blob = new Blob([content], { type: art.mime_type || "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = art.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const filtered = artifacts.filter(
    (a) =>
      a.name.toLowerCase().includes(filterQuery.toLowerCase()) ||
      a.artifact_id.toLowerCase().includes(filterQuery.toLowerCase()) ||
      a.mime_type.toLowerCase().includes(filterQuery.toLowerCase())
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "10px" }}>
      {/* Header & Controls */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <FolderOpen size={15} color="var(--ryu-gold-500)" />
          <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
            Space Artifacts ({artifacts.length})
          </span>
        </div>
        <button
          onClick={fetchArtifacts}
          disabled={loading}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--ryu-text-400)",
            cursor: "pointer",
            padding: "4px",
          }}
          title="Refresh artifacts"
        >
          <RefreshCw size={13} className={loading ? "spin" : ""} />
        </button>
      </div>

      {/* Filter */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          background: "var(--ryu-canvas)",
          border: "1px solid var(--ryu-border)",
          borderRadius: "6px",
          padding: "4px 8px",
        }}
      >
        <Search size={13} color="var(--ryu-text-400)" />
        <input
          type="text"
          placeholder="Filter artifacts..."
          value={filterQuery}
          onChange={(e) => setFilterQuery(e.target.value)}
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--ryu-text-100)",
            fontSize: "11px",
          }}
        />
        {filterQuery && (
          <button
            onClick={() => setFilterQuery("")}
            style={{ background: "transparent", border: "none", color: "var(--ryu-text-400)", cursor: "pointer" }}
          >
            <X size={12} />
          </button>
        )}
      </div>

      {/* Artifacts List or Content Viewer */}
      {selectedArtifact ? (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            background: "var(--ryu-canvas)",
            border: "1px solid var(--ryu-border)",
            borderRadius: "8px",
            overflow: "hidden",
          }}
        >
          {/* Selected Header */}
          <div
            style={{
              padding: "8px 10px",
              background: "var(--ryu-card-subtle)",
              borderBottom: "1px solid var(--ryu-border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "6px", overflow: "hidden" }}>
              <button
                onClick={() => {
                  setSelectedArtifact(null);
                  setArtifactContent(null);
                }}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--ryu-text-400)",
                  cursor: "pointer",
                  padding: "2px",
                }}
                title="Back to list"
              >
                <X size={14} />
              </button>
              <span
                style={{
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "var(--ryu-text-100)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={selectedArtifact.name}
              >
                {selectedArtifact.name}
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              {(selectedArtifact.mime_type === "text/html" || selectedArtifact.name.endsWith(".html")) && (
                <div style={{ display: "flex", background: "var(--ryu-card)", borderRadius: "4px", padding: "1px", border: "1px solid var(--ryu-border-subtle)" }}>
                  <button
                    onClick={() => setContentViewMode("preview")}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "3px",
                      background: contentViewMode === "preview" ? "var(--ryu-card-hover)" : "transparent",
                      color: contentViewMode === "preview" ? "var(--ryu-emerald-400)" : "var(--ryu-text-400)",
                      border: "none",
                      borderRadius: "3px",
                      padding: "2px 5px",
                      fontSize: "10px",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    <Eye size={10} /> Preview
                  </button>
                  <button
                    onClick={() => setContentViewMode("code")}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "3px",
                      background: contentViewMode === "code" ? "var(--ryu-card-hover)" : "transparent",
                      color: contentViewMode === "code" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                      border: "none",
                      borderRadius: "3px",
                      padding: "2px 5px",
                      fontSize: "10px",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    <Code size={10} /> Source
                  </button>
                </div>
              )}

              <button
                onClick={handleCopyContent}
                disabled={!artifactContent}
                style={{
                  background: "transparent",
                  border: "none",
                  color: copiedContent ? "var(--ryu-emerald-500)" : "var(--ryu-text-400)",
                  cursor: "pointer",
                  padding: "4px",
                }}
                title="Copy content"
              >
                <Copy size={13} />
              </button>

              <button
                onClick={() => handleDownload(selectedArtifact, artifactContent)}
                disabled={!artifactContent}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--ryu-text-400)",
                  cursor: "pointer",
                  padding: "4px",
                }}
                title="Download file"
              >
                <Download size={13} />
              </button>
            </div>
          </div>

          {/* Meta Info */}
          <div
            style={{
              padding: "4px 10px",
              background: "rgba(0, 0, 0, 0.2)",
              borderBottom: "1px solid var(--ryu-border-subtle)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: "10px",
              color: "var(--ryu-text-400)",
            }}
          >
            <span>{formatBytes(selectedArtifact.size_bytes)} • {selectedArtifact.mime_type}</span>
            <span
              onClick={() => handleCopySha(selectedArtifact.sha256)}
              style={{ cursor: "pointer", fontFamily: "var(--font-mono)" }}
              title="Click to copy full SHA-256"
            >
              SHA: {selectedArtifact.sha256.slice(0, 8)}... {copiedSha ? "(copied!)" : ""}
            </span>
          </div>

          {/* Viewer Area */}
          <div style={{ flex: 1, overflow: "auto", position: "relative" }}>
            {loadingContent ? (
              <div style={{ padding: "20px", textAlign: "center", color: "var(--ryu-text-400)", fontSize: "11px" }}>
                Loading artifact content...
              </div>
            ) : contentViewMode === "preview" && (selectedArtifact.mime_type === "text/html" || selectedArtifact.name.endsWith(".html")) ? (
              <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "5px",
                    padding: "3px 8px",
                    background: "#18181b",
                    fontSize: "9px",
                    color: "var(--ryu-emerald-400)",
                    borderBottom: "1px solid #27272a",
                  }}
                >
                  <Shield size={10} />
                  <span>Zero-Privilege Sandbox (Isolated iframe, scripts enabled, same-origin denied)</span>
                </div>
                <iframe
                  sandbox="allow-scripts"
                  srcDoc={artifactContent || ""}
                  style={{
                    flex: 1,
                    width: "100%",
                    minHeight: "280px",
                    border: "none",
                    backgroundColor: "#ffffff",
                  }}
                  title="Artifact HTML Preview"
                />
              </div>
            ) : (
              <pre
                style={{
                  margin: 0,
                  padding: "10px",
                  fontSize: "11px",
                  fontFamily: "var(--font-mono)",
                  color: "var(--ryu-text-100)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                <code>{artifactContent}</code>
              </pre>
            )}
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "4px" }}>
          {filtered.length === 0 ? (
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
              {filterQuery ? "No artifacts match filter." : "No artifacts generated in this space yet."}
            </div>
          ) : (
            filtered.map((art) => {
              const isHtml = art.mime_type === "text/html" || art.name.endsWith(".html");
              return (
                <div
                  key={art.artifact_id}
                  onClick={() => handleSelectArtifact(art)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "8px 10px",
                    background: "var(--ryu-card)",
                    border: "1px solid var(--ryu-border)",
                    borderRadius: "6px",
                    cursor: "pointer",
                    transition: "border-color 0.15s ease",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--ryu-border-subtle)")}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--ryu-border)")}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", overflow: "hidden" }}>
                    {isHtml ? (
                      <FileCode size={15} color="var(--ryu-emerald-400)" />
                    ) : (
                      <FileText size={15} color="var(--ryu-gold-400)" />
                    )}
                    <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
                      <span
                        style={{
                          fontSize: "12px",
                          fontWeight: 500,
                          color: "var(--ryu-text-100)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {art.name}
                      </span>
                      <span style={{ fontSize: "10px", color: "var(--ryu-text-600)" }}>
                        {formatBytes(art.size_bytes)} • {art.mime_type}
                      </span>
                    </div>
                  </div>

                  <span style={{ fontSize: "10px", color: "var(--ryu-text-600)", fontFamily: "var(--font-mono)" }}>
                    {art.sha256.slice(0, 6)}...
                  </span>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
};
