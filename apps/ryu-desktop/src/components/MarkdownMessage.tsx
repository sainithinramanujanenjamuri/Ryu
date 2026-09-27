import React, { useState } from "react";
import { Check, Code, Copy, Eye, Shield } from "lucide-react";

interface MarkdownMessageProps {
  content: string;
}

export const MarkdownMessage: React.FC<MarkdownMessageProps> = ({ content }) => {
  // Simple parser to separate text segments from fenced code blocks (```lang ... ```)
  const segments = React.useMemo(() => {
    const regex = /```(\w*)\n([\s\S]*?)```/g;
    const parts: Array<{ type: "text" | "code"; lang?: string; code?: string; text?: string }> = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = regex.exec(content)) !== null) {
      if (match.index > lastIndex) {
        parts.push({
          type: "text",
          text: content.slice(lastIndex, match.index),
        });
      }
      parts.push({
        type: "code",
        lang: match[1] || "plaintext",
        code: match[2],
      });
      lastIndex = regex.lastIndex;
    }

    if (lastIndex < content.length) {
      parts.push({
        type: "text",
        text: content.slice(lastIndex),
      });
    }

    return parts;
  }, [content]);

  return (
    <div
      className="markdown-content selectable-text"
      style={{
        fontSize: "13px",
        lineHeight: "1.6",
        color: "var(--ryu-text-200)",
        userSelect: "text",
        WebkitUserSelect: "text",
      }}
    >
      {segments.map((seg, idx) => {
        if (seg.type === "code" && seg.code !== undefined) {
          return <CodeBlock key={idx} language={seg.lang || "code"} code={seg.code} />;
        }
        return <FormattedText key={idx} text={seg.text || ""} />;
      })}
    </div>
  );
};

export const CodeBlock: React.FC<{ language: string; code: string; defaultTab?: "code" | "preview" }> = ({
  language,
  code,
  defaultTab = "code",
}) => {
  const isHtml = language.toLowerCase() === "html" || language.toLowerCase() === "htm";
  const [activeTab, setActiveTab] = useState<"code" | "preview">(isHtml ? defaultTab : "code");
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-container" style={{ margin: "10px 0", borderRadius: "8px", overflow: "hidden", border: "1px solid var(--ryu-border)" }}>
      <div className="code-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 12px", background: "var(--ryu-card-subtle)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ textTransform: "uppercase", fontWeight: 600, letterSpacing: "0.5px", fontSize: "11px", color: "var(--ryu-text-400)" }}>
            {language}
          </span>

          {isHtml && (
            <div style={{ display: "flex", background: "var(--ryu-card)", borderRadius: "4px", padding: "2px", border: "1px solid var(--ryu-border-subtle)" }}>
              <button
                onClick={() => setActiveTab("code")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  background: activeTab === "code" ? "var(--ryu-card-hover)" : "transparent",
                  color: activeTab === "code" ? "var(--ryu-text-100)" : "var(--ryu-text-400)",
                  border: "none",
                  borderRadius: "3px",
                  padding: "2px 6px",
                  fontSize: "10px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                <Code size={11} /> Code
              </button>
              <button
                onClick={() => setActiveTab("preview")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  background: activeTab === "preview" ? "var(--ryu-card-hover)" : "transparent",
                  color: activeTab === "preview" ? "var(--ryu-emerald-400)" : "var(--ryu-text-400)",
                  border: "none",
                  borderRadius: "3px",
                  padding: "2px 6px",
                  fontSize: "10px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                <Eye size={11} /> Preview
              </button>
            </div>
          )}
        </div>

        <button
          onClick={handleCopy}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "5px",
            background: "transparent",
            border: "none",
            color: copied ? "var(--ryu-emerald-500)" : "var(--ryu-text-400)",
            fontSize: "11px",
            cursor: "pointer",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
          title="Copy code"
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>

      {isHtml && activeTab === "preview" ? (
        <div style={{ position: "relative", width: "100%", background: "#ffffff", borderTop: "1px solid var(--ryu-border)" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "5px",
              padding: "4px 10px",
              background: "#18181b",
              borderBottom: "1px solid #27272a",
              fontSize: "10px",
              color: "var(--ryu-emerald-400)",
            }}
          >
            <Shield size={11} />
            <span>Zero-Privilege Sandbox (Isolated iframe, scripts enabled, same-origin denied)</span>
          </div>
          <iframe
            sandbox="allow-scripts"
            srcDoc={code}
            style={{
              width: "100%",
              height: "280px",
              border: "none",
              display: "block",
              backgroundColor: "#ffffff",
            }}
            title="Sandboxed HTML Preview"
          />
        </div>
      ) : (
        <pre className="code-body" style={{ margin: 0, padding: "12px", background: "var(--ryu-canvas)", overflowX: "auto" }}>
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
};

const FormattedText: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split("\n");
  return (
    <div>
      {lines.map((line, idx) => {
        // Headers ###
        if (line.startsWith("### ")) {
          return (
            <h3
              key={idx}
              style={{
                fontSize: "14px",
                fontWeight: 600,
                color: "var(--ryu-text-100)",
                margin: "12px 0 6px 0",
              }}
            >
              {line.slice(4)}
            </h3>
          );
        }
        if (line.startsWith("#### ")) {
          return (
            <h4
              key={idx}
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: "var(--ryu-gold-400)",
                margin: "10px 0 4px 0",
              }}
            >
              {line.slice(5)}
            </h4>
          );
        }
        // Bullets
        if (line.startsWith("- ") || line.startsWith("* ")) {
          return (
            <div key={idx} style={{ display: "flex", gap: "8px", margin: "2px 0 2px 8px" }}>
              <span style={{ color: "var(--ryu-emerald-500)" }}>•</span>
              <div>{renderInlineFormatting(line.slice(2))}</div>
            </div>
          );
        }
        if (!line.trim()) {
          return <div key={idx} style={{ height: "6px" }} />;
        }
        return <div key={idx}>{renderInlineFormatting(line)}</div>;
      })}
    </div>
  );
};

function renderInlineFormatting(str: string): React.ReactNode {
  // Inline code `...` and bold **...**
  const parts = str.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          style={{
            background: "var(--ryu-card-hover)",
            padding: "2px 5px",
            borderRadius: "4px",
            fontSize: "11px",
            color: "var(--ryu-emerald-500)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} style={{ color: "var(--ryu-text-100)", fontWeight: 600 }}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}
