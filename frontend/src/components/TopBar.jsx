import React, { useState } from "react";
import {
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Play,
  Copy,
  Check,
  Radio,
  ExternalLink,
  ChevronRight,
  ShieldCheck,
  HeartPulse,
  Pill
} from "lucide-react";
import { MODULES, MODULE_TOOLS } from "./AppSidebar.jsx";

export default function TopBar({
  activeModule = "news",
  activePage = "dashboard",
  sidebarOpen = true,
  setSidebarOpen,
  onQuickRun,
  onRefresh,
  isRefreshing = false,
  onCopyBrief,
}) {
  const [copied, setCopied] = useState(false);

  const mod = MODULES.find((m) => m.key === activeModule) || MODULES[0];
  const tools = MODULE_TOOLS[activeModule] || [];
  const page = tools.find((t) => t.id === activePage) || { label: "Overview" };

  const handleCopy = () => {
    if (onCopyBrief) {
      onCopyBrief();
    } else {
      navigator.clipboard?.writeText("CompetePulse: Morning Update Brief copied.");
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2400);
  };

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 20,
        height: 52,
        backgroundColor: "var(--ev-white)",
        borderBottom: "1px solid var(--ev-outline-soft)",
        boxShadow: "var(--ev-shadow-1)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 20px",
        flexShrink: 0,
      }}
    >
      {/* Left: Breadcrumbs & Therapeutic Area Context */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button
          type="button"
          onClick={() => setSidebarOpen((v) => !v)}
          title={sidebarOpen ? "Collapse navigation" : "Expand navigation"}
          style={{
            background: "transparent",
            border: "1px solid var(--ev-outline)",
            borderRadius: 6,
            padding: "5px 7px",
            color: "var(--ev-dark-purple)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "all 0.15s ease",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-purple-50)")}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
        >
          {sidebarOpen ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ev-muted)" }}>
          <span style={{ fontWeight: 700, color: "var(--ev-dark-purple)" }}>CompetePulse</span>
          <ChevronRight size={12} color="var(--ev-subtle)" />
          <span>{mod.label}</span>
          <ChevronRight size={12} color="var(--ev-subtle)" />
          <span style={{ fontWeight: 600, color: "var(--ev-purple)" }}>{page.label}</span>
        </div>

        {/* Therapeutic Area & Indication Pill */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 10px",
            borderRadius: 6,
            backgroundColor: "var(--ev-surface-2)",
            border: "1px solid var(--ev-outline)",
            fontSize: 11.5,
            fontWeight: 500,
            color: "var(--ev-dark-purple)",
            marginLeft: 4,
          }}
        >
          <HeartPulse size={13} color="var(--ev-red)" />
          <span>TA: <strong>Hepatology</strong></span>
          <span style={{ color: "var(--ev-subtle)" }}>•</span>
          <span>Indication: <strong style={{ color: "var(--ev-purple)" }}>MASH / MASLD</strong></span>
          <span style={{ fontSize: 10, color: "var(--ev-subtle)" }}>(Scope: MetALD)</span>
        </div>
      </div>

      {/* Right: Status Pill & Quick Action Bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {/* Pipeline Heartbeat */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 10px",
            borderRadius: 999,
            backgroundColor: "var(--ev-purple-50)",
            border: "1px solid var(--ev-purple-200)",
            fontSize: 11.5,
            fontWeight: 600,
            color: "var(--ev-purple)",
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              backgroundColor: "#107c41",
              boxShadow: "0 0 6px rgba(16, 124, 65, 0.5)",
            }}
          />
          <span>Pipeline Ready</span>
          <span style={{ color: "var(--ev-subtle)", fontWeight: 400 }}>• 18:15 IST</span>
        </div>

        {/* Sync Button */}
        <button
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          title="Sync with FastAPI JSON backend"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 11px",
            borderRadius: 6,
            border: "1px solid var(--ev-outline)",
            backgroundColor: "var(--ev-white)",
            color: "var(--ev-dark-purple)",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            transition: "all 0.15s ease",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-surface-2)")}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-white)")}
        >
          <RefreshCw size={13} className={isRefreshing ? "spin" : ""} />
          <span>Sync</span>
        </button>

        {/* Copy Brief Button with Toast Feedback */}
        <button
          type="button"
          onClick={handleCopy}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            borderRadius: 6,
            border: "1px solid var(--ev-outline)",
            backgroundColor: copied ? "#ecfdf5" : "var(--ev-white)",
            borderColor: copied ? "#107c41" : "var(--ev-outline)",
            color: copied ? "#107c41" : "var(--ev-dark-purple)",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            transition: "all 0.18s ease",
          }}
        >
          {copied ? <Check size={13} color="#107c41" /> : <Copy size={13} />}
          <span>{copied ? "Copied to Clipboard!" : "Copy Brief"}</span>
        </button>

        {/* Quick Run Primary Action */}
        <button
          type="button"
          onClick={onQuickRun}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 14px",
            borderRadius: 6,
            border: "none",
            backgroundColor: "var(--ev-purple)",
            color: "#ffffff",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
            boxShadow: "0 1px 3px rgba(67, 34, 77, 0.25)",
            transition: "all 0.15s ease",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-dark-purple)")}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-purple)")}
        >
          <Play size={13} fill="#ffffff" />
          <span>Run Pipeline</span>
        </button>
      </div>
    </header>
  );
}
