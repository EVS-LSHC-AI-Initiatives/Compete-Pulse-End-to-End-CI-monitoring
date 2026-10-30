import React, { useState } from "react";
import {
  X,
  ExternalLink,
  ShieldCheck,
  AlertCircle,
  Building2,
  Calendar,
  FileText,
  Sparkles,
  Check,
  Bookmark,
  Trash2,
  Tag,
  Link,
  Save,
  MessageSquare
} from "lucide-react";
import { PriorityBadge, DispositionBadge, ScopeBadge } from "./StatusBadge.jsx";

export default function DetailDrawer({ item, onClose, onUpdateDisposition }) {
  if (!item) return null;

  const [activeTab, setActiveTab] = useState("analysis");
  const [notes, setNotes] = useState(item.reviewer_notes || "");
  const [savedMessage, setSavedMessage] = useState(false);

  const handleAction = (disposition, priority = null) => {
    if (onUpdateDisposition) {
      onUpdateDisposition(item.id, {
        disposition,
        priority: priority || item.priority,
        notes,
        selected_for_draft: disposition === "Approved for Morning Brief",
      });
      setSavedMessage(true);
      setTimeout(() => setSavedMessage(false), 2000);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 580,
        maxWidth: "92vw",
        backgroundColor: "var(--ev-white)",
        boxShadow: "var(--ev-shadow-3)",
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        borderLeft: "1px solid var(--ev-outline)",
        animation: "ev-slide-in 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
      }}
    >
      <style>{`
        @keyframes ev-slide-in {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>

      {/* Drawer Header */}
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--ev-outline-soft)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          backgroundColor: "var(--ev-surface-2)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 800,
              color: "var(--ev-purple)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              backgroundColor: "var(--ev-purple-50)",
              padding: "2px 8px",
              borderRadius: 4,
              border: "1px solid var(--ev-purple-200)",
            }}
          >
            {item.id}
          </span>
          <ScopeBadge scope={item.mash_scope} />
          <PriorityBadge priority={item.priority} />
        </div>

        <button
          type="button"
          onClick={onClose}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--ev-subtle)",
            cursor: "pointer",
            padding: 4,
            borderRadius: 4,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--ev-dark-purple)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--ev-subtle)")}
        >
          <X size={18} />
        </button>
      </div>

      {/* Tabs Bar */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--ev-outline-soft)",
          padding: "0 20px",
          backgroundColor: "var(--ev-white)",
          gap: 16,
        }}
      >
        <button
          type="button"
          onClick={() => setActiveTab("analysis")}
          style={{
            padding: "10px 0",
            border: "none",
            borderBottom: activeTab === "analysis" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            fontSize: 12.5,
            fontWeight: activeTab === "analysis" ? 700 : 500,
            color: activeTab === "analysis" ? "var(--ev-purple)" : "var(--ev-muted)",
            cursor: "pointer",
          }}
        >
          AI Analysis & Rationale
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("meta")}
          style={{
            padding: "10px 0",
            border: "none",
            borderBottom: activeTab === "meta" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            fontSize: 12.5,
            fontWeight: activeTab === "meta" ? 700 : 500,
            color: activeTab === "meta" ? "var(--ev-purple)" : "var(--ev-muted)",
            cursor: "pointer",
          }}
        >
          Source Metadata & Feed
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("notes")}
          style={{
            padding: "10px 0",
            border: "none",
            borderBottom: activeTab === "notes" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            fontSize: 12.5,
            fontWeight: activeTab === "notes" ? 700 : 500,
            color: activeTab === "notes" ? "var(--ev-purple)" : "var(--ev-muted)",
            cursor: "pointer",
          }}
        >
          Reviewer Notes
        </button>
      </div>

      {/* Body Content */}
      <div style={{ flex: 1, padding: "20px 24px", overflowY: "auto" }}>
        {/* Article Headline */}
        <h2
          style={{
            margin: "0 0 12px",
            fontSize: 16,
            fontWeight: 700,
            lineHeight: 1.4,
            color: "var(--ev-dark-purple)",
          }}
        >
          {item.title}
        </h2>

        {/* Source info bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 14,
            fontSize: 12,
            color: "var(--ev-muted)",
            marginBottom: 20,
            paddingBottom: 14,
            borderBottom: "1px solid var(--ev-outline-soft)",
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <Building2 size={13} color="var(--ev-purple)" />
            <strong style={{ color: "var(--ev-gray)" }}>{item.source}</strong>
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <Calendar size={13} />
            <span>{item.published_date || item.publication_date || "Today"}</span>
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <Tag size={13} />
            <span>{item.feed_category || "News Feed"}</span>
          </span>
          {item.source_url && (
            <a
              href={item.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                color: "var(--ev-blue)",
                textDecoration: "none",
                fontWeight: 600,
                marginLeft: "auto",
              }}
            >
              <span>Visit Article</span>
              <ExternalLink size={12} />
            </a>
          )}
        </div>

        {activeTab === "analysis" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            {/* AI Strategic Takeaway Card */}
            <div
              style={{
                backgroundColor: "var(--ev-purple-50)",
                border: "1px solid var(--ev-purple-200)",
                borderRadius: 8,
                padding: "16px 18px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--ev-purple)",
                  fontSize: 12,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 8,
                }}
              >
                <Sparkles size={14} />
                <span>AI Strategic Takeaway</span>
              </div>
              <p
                style={{
                  margin: 0,
                  fontSize: 13.5,
                  lineHeight: 1.6,
                  color: "var(--ev-dark-purple)",
                  fontWeight: 500,
                }}
              >
                {item.takeaway || item.summary || "No automated summary available."}
              </p>
            </div>

            {/* MASH Scientific Rationale Card */}
            <div
              style={{
                backgroundColor: "var(--ev-surface-2)",
                border: "1px solid var(--ev-outline)",
                borderRadius: 8,
                padding: "16px 18px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--ev-gray)",
                  fontSize: 12,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 8,
                }}
              >
                <ShieldCheck size={14} color="var(--ev-blue)" />
                <span>Therapeutic Scope & Rationale</span>
              </div>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--ev-gray)" }}>
                {item.rationale || "Evaluated by Parallel AI agent against MASH/MASLD target mechanisms and inclusion criteria."}
              </p>
            </div>

            {/* Current Disposition */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 14px", backgroundColor: "var(--ev-surface-3)", borderRadius: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ev-muted)" }}>Current Workflow Status:</span>
              <DispositionBadge disposition={item.disposition} />
            </div>
          </div>
        )}

        {activeTab === "meta" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ padding: "12px 14px", backgroundColor: "var(--ev-surface-2)", borderRadius: 6, border: "1px solid var(--ev-outline-soft)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ev-subtle)", textTransform: "uppercase" }}>Ingestion Feed Source</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ev-dark-purple)", marginTop: 4 }}>{item.source}</div>
            </div>
            <div style={{ padding: "12px 14px", backgroundColor: "var(--ev-surface-2)", borderRadius: 6, border: "1px solid var(--ev-outline-soft)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ev-subtle)", textTransform: "uppercase" }}>Primary URL</div>
              <div style={{ fontSize: 12, wordBreak: "break-all", color: "var(--ev-blue)", marginTop: 4 }}>{item.source_url || "N/A"}</div>
            </div>
            <div style={{ padding: "12px 14px", backgroundColor: "var(--ev-surface-2)", borderRadius: 6, border: "1px solid var(--ev-outline-soft)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ev-subtle)", textTransform: "uppercase" }}>Selected For Brief Draft</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: item.selected_for_draft ? "#107c41" : "var(--ev-muted)", marginTop: 4 }}>
                {item.selected_for_draft ? "Yes — Included in Client Update" : "No — Excluded or Pending"}
              </div>
            </div>
          </div>
        )}

        {activeTab === "notes" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label style={{ fontSize: 12, fontWeight: 700, color: "var(--ev-dark-purple)" }}>Analyst Comments & Context:</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add strategic insight or notes for team members..."
              rows={6}
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: 6,
                border: "1px solid var(--ev-outline-strong)",
                fontFamily: "'Inter', sans-serif",
                fontSize: 13,
                lineHeight: 1.5,
              }}
            />
            <button
              type="button"
              onClick={() => handleAction(item.disposition)}
              style={{
                alignSelf: "flex-start",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "7px 14px",
                borderRadius: 6,
                backgroundColor: "var(--ev-purple)",
                color: "#ffffff",
                border: "none",
                fontWeight: 600,
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              <Save size={13} />
              <span>Save Notes</span>
            </button>
          </div>
        )}
      </div>

      {/* Action Footer */}
      <div
        style={{
          padding: "16px 24px",
          borderTop: "1px solid var(--ev-outline-soft)",
          backgroundColor: "var(--ev-surface-2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <div style={{ fontSize: 11.5, color: savedMessage ? "#107c41" : "var(--ev-muted)", fontWeight: 600 }}>
          {savedMessage ? "✓ Changes Saved to Backend JSON" : "Disposition Controls:"}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            onClick={() => handleAction("Rejected")}
            style={{
              padding: "7px 12px",
              borderRadius: 6,
              border: "1px solid var(--ev-outline)",
              backgroundColor: "var(--ev-white)",
              color: "var(--ev-muted)",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <Trash2 size={13} />
            <span>Reject</span>
          </button>

          <button
            type="button"
            onClick={() => handleAction("Kept for Watchlist")}
            style={{
              padding: "7px 12px",
              borderRadius: 6,
              border: "1px solid var(--ev-outline)",
              backgroundColor: "var(--ev-white)",
              color: "var(--ev-blue-deep)",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <Bookmark size={13} />
            <span>Watchlist</span>
          </button>

          <button
            type="button"
            onClick={() => handleAction("Approved for Morning Brief", "High")}
            style={{
              padding: "7px 14px",
              borderRadius: 6,
              border: "none",
              backgroundColor: "var(--ev-red)",
              color: "#ffffff",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
              boxShadow: "0 1px 3px rgba(224, 4, 78, 0.25)",
            }}
          >
            <Check size={13} />
            <span>Approve for Brief</span>
          </button>
        </div>
      </div>
    </div>
  );
}
