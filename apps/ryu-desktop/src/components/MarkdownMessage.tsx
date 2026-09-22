import React, { useState } from "react";
import { Check, Copy } from "lucide-react";

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
    <div style={{ fontSize: "13px", lineHeight: "1.6", color: "var(--ryu-text-200)" }}>
      {segments.map((seg, idx) => {
        if (seg.type === "code" && seg.code !== undefined) {
          return <CodeBlock key={idx} language={seg.lang || "code"} code={seg.code} />;
        }
        return <FormattedText key={idx} text={seg.text || ""} />;
      })}
    </div>
  );
};

const CodeBlock: React.FC<{ language: string; code: string }> = ({ language, code }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-container" style={{ margin: "10px 0" }}>
      <div className="code-header">
        <span style={{ textTransform: "uppercase", fontWeight: 600, letterSpacing: "0.5px" }}>
          {language}
        </span>
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
      <pre className="code-body">
        <code>{code}</code>
      </pre>
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

