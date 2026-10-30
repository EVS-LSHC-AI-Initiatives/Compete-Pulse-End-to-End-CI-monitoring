import React, { useState } from "react";
import {
  Activity,
  Newspaper,
  Mail,
  FlaskConical,
  Rss,
  PlayCircle,
  SlidersHorizontal,
  ChevronRight,
  Pin,
  Sparkles,
  Server,
  Layers,
  Database,
  CheckCircle2,
  AlertCircle,
  HeartPulse,
  Pill
} from "lucide-react";
import BrandLockup from "../BrandLockup.jsx";

// Top-Level CompetePulse Modules (Panel 1)
export const MODULES = [
  {
    key: "news",
    label: "CI News & Triage",
    icon: Newspaper,
    description: "Multi-source news ingestion & AI triage",
  },
  {
    key: "clinical",
    label: "Clinical Trials Radar",
    icon: FlaskConical,
    description: "Registry tracking & change detection",
  },
  {
    key: "operations",
    label: "Pipeline & Operations",
    icon: Server,
    description: "OPML feed health & orchestration",
  },
];

// Sub-pages/Tools within each module (Panel 2)
export const MODULE_TOOLS = {
  news: [
    { id: "dashboard", label: "Executive Overview", icon: Activity, badge: "Live" },
    { id: "triage", label: "News AI Triage", icon: Newspaper, badge: "18 High", badgeColor: "var(--ev-red)" },
    { id: "morning-update", label: "Client Update Brief", icon: Mail, badge: "Draft", badgeColor: "var(--ev-purple)" },
  ],
  clinical: [
    { id: "clinical-trials", label: "Trial Radar & Diffs", icon: FlaskConical, badge: "5 Alerts", badgeColor: "#d97706" },
  ],
  operations: [
    { id: "feeds", label: "Feed Health Monitor", icon: Rss, badge: "592 Feeds" },
    { id: "runner", label: "Pipeline Orchestrator", icon: PlayCircle, badge: "Run" },
    { id: "settings", label: "Mail & Agent Settings", icon: SlidersHorizontal },
  ],
};

const MODULES_PANEL_WIDTH = 236;
const MODULES_PANEL_COLLAPSED_WIDTH = 58;
const PIN_KEY = "competepulse_modules_pinned";

export default function AppSidebar({
  activeModule = "news",
  setActiveModule,
  activePage = "dashboard",
  setActivePage,
  sidebarOpen = true,
  setSidebarOpen,
  apiConnected = true,
}) {
  const [modulesPinned, setModulesPinned] = useState(() => {
    try {
      const saved = localStorage.getItem(PIN_KEY);
      return saved === null ? true : saved === "1";
    } catch {
      return true;
    }
  });

  const [modulesHovering, setModulesHovering] = useState(false);

  const togglePin = () => {
    setModulesPinned((v) => {
      const next = !v;
      try {
        localStorage.setItem(PIN_KEY, next ? "1" : "0");
      } catch {}
      return next;
    });
  };

  const isCollapsed = !modulesPinned && !modulesHovering;
  const currentTools = MODULE_TOOLS[activeModule] || [];

  return (
    <aside
      style={{
        display: "flex",
        height: "100%",
        flexShrink: 0,
        backgroundColor: "var(--ev-dark-purple)",
        borderRight: "1px solid var(--ev-outline-soft)",
        userSelect: "none",
        zIndex: 30,
        boxShadow: "var(--ev-shadow-1)",
      }}
    >
      {/* ── PANEL 1: MODULES BAR (Leftmost, 236px) ── */}
      <div
        onMouseEnter={() => !modulesPinned && setModulesHovering(true)}
        onMouseLeave={() => setModulesHovering(false)}
        style={{
          width: isCollapsed ? MODULES_PANEL_COLLAPSED_WIDTH : MODULES_PANEL_WIDTH,
          minWidth: isCollapsed ? MODULES_PANEL_COLLAPSED_WIDTH : MODULES_PANEL_WIDTH,
          display: "flex",
          flexDirection: "column",
          backgroundColor: "#260b2d",
          borderRight: "1px solid rgba(255, 255, 255, 0.08)",
          transition: "width 0.2s cubic-bezier(0.16, 1, 0.3, 1), min-width 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
          overflow: "hidden",
        }}
      >
        {/* Brand Header */}
        <div
          style={{
            padding: isCollapsed ? "16px 0 14px" : "16px 14px 14px",
            borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
            display: "flex",
            alignItems: "center",
            justifyContent: isCollapsed ? "center" : "flex-start",
          }}
        >
          <BrandLockup
            size={14}
            dark={false}
            showProduct={!isCollapsed}
            collapsed={isCollapsed}
          />
        </div>

        {/* Modules Section Header with Pin Button */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: isCollapsed ? "center" : "space-between",
            padding: isCollapsed ? "12px 0 6px" : "14px 14px 6px",
          }}
        >
          {!isCollapsed && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 800,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: "rgba(255, 255, 255, 0.5)",
              }}
            >
              Intelligence Modules
            </span>
          )}
          <button
            type="button"
            title={modulesPinned ? "Collapse module panel" : "Pin module panel open"}
            onClick={togglePin}
            style={{
              background: "transparent",
              border: "none",
              color: modulesPinned ? "var(--ev-light-blue)" : "rgba(255, 255, 255, 0.45)",
              cursor: "pointer",
              padding: 4,
              borderRadius: 4,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Pin
              size={13}
              style={{
                transform: modulesPinned ? "rotate(0deg)" : "rotate(45deg)",
                transition: "transform 0.15s ease",
              }}
            />
          </button>
        </div>

        {/* Module Rows */}
        <div style={{ flex: 1, padding: isCollapsed ? "4px 6px" : "4px 8px", display: "flex", flexDirection: "column", gap: 4 }}>
          {MODULES.map((mod) => {
            const Icon = mod.icon;
            const isActive = activeModule === mod.key;

            return (
              <button
                key={mod.key}
                type="button"
                onClick={() => {
                  setActiveModule(mod.key);
                  const tools = MODULE_TOOLS[mod.key];
                  if (tools && tools.length > 0) {
                    setActivePage(tools[0].id);
                  }
                }}
                title={mod.label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  width: "100%",
                  padding: isCollapsed ? "10px 0" : "10px 10px",
                  justifyContent: isCollapsed ? "center" : "flex-start",
                  borderRadius: 6,
                  border: "none",
                  borderLeft: isActive ? "3px solid var(--ev-red)" : "3px solid transparent",
                  backgroundColor: isActive ? "rgba(255, 255, 255, 0.12)" : "transparent",
                  color: isActive ? "#ffffff" : "rgba(255, 255, 255, 0.72)",
                  cursor: "pointer",
                  textAlign: "left",
                  transition: "all 0.15s ease",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = "rgba(255, 255, 255, 0.06)";
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                <Icon size={17} color={isActive ? "var(--ev-light-blue)" : "rgba(255, 255, 255, 0.7)"} />
                {!isCollapsed && (
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: isActive ? 700 : 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {mod.label}
                    </div>
                  </div>
                )}
                {!isCollapsed && isActive && (
                  <ChevronRight size={13} color="rgba(255, 255, 255, 0.5)" />
                )}
              </button>
            );
          })}
        </div>

        {/* Module Bar Footer */}
        {!isCollapsed && (
          <div
            style={{
              padding: "12px 14px",
              borderTop: "1px solid rgba(255, 255, 255, 0.08)",
              fontSize: 10,
              color: "rgba(255, 255, 255, 0.45)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>CompetePulse v2.4</span>
            <span style={{ color: apiConnected ? "#107c41" : "var(--ev-red)", display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", backgroundColor: apiConnected ? "#107c41" : "var(--ev-red)" }} />
              {apiConnected ? "API:8792" : "Offline"}
            </span>
          </div>
        )}
      </div>

      {/* ── PANEL 2: ACTIVE MODULE TOOLS / STEPS (Secondary Sidebar) ── */}
      <div
        style={{
          width: 230,
          display: "flex",
          flexDirection: "column",
          backgroundColor: "var(--ev-sidebar)",
          borderRight: "1px solid rgba(255, 255, 255, 0.08)",
        }}
      >
        {/* Module Context Title Header */}
        <div
          style={{
            padding: "16px 16px 14px",
            borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
          }}
        >
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ev-light-blue)" }}>
            Current Module
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: "#ffffff", marginTop: 2 }}>
            {MODULES.find((m) => m.key === activeModule)?.label}
          </div>
        </div>

        {/* Target Clinical Scope Callout */}
        <div
          style={{
            margin: "10px 10px 6px",
            padding: "8px 10px",
            borderRadius: 6,
            backgroundColor: "rgba(255, 255, 255, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--ev-light-blue)", fontWeight: 700, textTransform: "uppercase", fontSize: 9.5 }}>
            <HeartPulse size={11} color="var(--ev-red)" />
            <span>Workflow Scope</span>
          </div>
          <div style={{ fontWeight: 800, color: "#ffffff", fontSize: 12, marginTop: 3 }}>
            MASH / MASLD
          </div>
          <div style={{ fontSize: 10, color: "rgba(255, 255, 255, 0.6)", marginTop: 1 }}>
            TA: Hepatology & Metabolic
          </div>
        </div>

        {/* Step / Tool List */}
        <div style={{ flex: 1, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 4, overflowY: "auto" }}>
          {currentTools.map((tool) => {
            const Icon = tool.icon;
            const isSelected = activePage === tool.id;

            return (
              <button
                key={tool.id}
                type="button"
                onClick={() => setActivePage(tool.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  width: "100%",
                  padding: "9px 12px",
                  borderRadius: 6,
                  border: "none",
                  backgroundColor: isSelected ? "rgba(255, 255, 255, 0.16)" : "transparent",
                  color: isSelected ? "#ffffff" : "rgba(255, 255, 255, 0.8)",
                  cursor: "pointer",
                  textAlign: "left",
                  transition: "background 0.15s ease",
                }}
                onMouseEnter={(e) => {
                  if (!isSelected) e.currentTarget.style.backgroundColor = "rgba(255, 255, 255, 0.08)";
                }}
                onMouseLeave={(e) => {
                  if (!isSelected) e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                <Icon size={16} color={isSelected ? "var(--ev-light-blue)" : "rgba(255, 255, 255, 0.65)"} />
                <span style={{ flex: 1, fontSize: 12.5, fontWeight: isSelected ? 700 : 500 }}>
                  {tool.label}
                </span>
                {tool.badge && (
                  <span
                    style={{
                      fontSize: 9.5,
                      fontWeight: 700,
                      padding: "2px 6px",
                      borderRadius: 10,
                      backgroundColor: tool.badgeColor ? `${tool.badgeColor}33` : "rgba(255, 255, 255, 0.15)",
                      color: tool.badgeColor || "#ffffff",
                    }}
                  >
                    {tool.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Hypothetical Lead Analyst User Card */}
        <div
          style={{
            padding: "12px 14px",
            borderTop: "1px solid rgba(255, 255, 255, 0.08)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              backgroundColor: "var(--ev-purple)",
              border: "1.5px solid rgba(255, 255, 255, 0.2)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 12,
              fontWeight: 700,
              color: "#ffffff",
            }}
          >
            AM
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#ffffff", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              Dr. Alexander Morgan
            </div>
            <div style={{ fontSize: 10, color: "rgba(255, 255, 255, 0.55)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              Lead CI Strategist
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
