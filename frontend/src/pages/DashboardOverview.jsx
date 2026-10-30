import React, { useState, useEffect } from "react";
import {
  Rss,
  Sparkles,
  Mail,
  FlaskConical,
  Play,
  ArrowRight,
  TrendingUp,
  ShieldCheck,
  AlertTriangle,
  Clock,
  ExternalLink,
  ChevronRight,
  Activity,
  CheckCircle2,
  Layers,
  Search,
  Filter
} from "lucide-react";
import { C, SHADOWS, RADIUS } from "../theme.js";
import { api } from "../api.js";
import { PageLoader } from "../components/loaders/index.jsx";
import { PriorityBadge, DispositionBadge } from "../components/StatusBadge.jsx";
import DetailDrawer from "../components/DetailDrawer.jsx";
import PipelineStepper from "../components/PipelineStepper.jsx";

export default function DashboardOverview({ onNavigate }) {
  const [metrics, setMetrics] = useState(null);
  const [recentItems, setRecentItems] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const m = await api.getMetrics();
        const res = await api.getTriagedItems({});
        const items = Array.isArray(res) ? res : (res?.items || []);
        setMetrics(m);
        setRecentItems(items.slice(0, 6));
      } catch (err) {
        console.error("Failed to load metrics", err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading || !metrics) {
    return <PageLoader text="Loading CompetePulse Intelligence..." />;
  }

  const kpis = [
    {
      title: "Active Feeds Monitored",
      value: metrics.totalFeeds || 592,
      sub: `${metrics.healthyFeeds || 581} healthy · 5 OPML sets`,
      icon: Rss,
      accent: "var(--ev-purple)",
      bg: "var(--ev-purple-50)",
      action: () => onNavigate?.("feeds"),
    },
    {
      title: "Extracted Today",
      value: metrics.rawExtractions || 148,
      sub: `${metrics.deduped || 84} unique candidates`,
      icon: Layers,
      accent: "var(--ev-blue)",
      bg: "var(--ev-blue-50)",
      action: () => onNavigate?.("triage"),
    },
    {
      title: "High Priority Signals",
      value: metrics.highPriority || 18,
      sub: `${metrics.inScope || 84} in therapeutic scope`,
      icon: AlertTriangle,
      accent: "var(--ev-red)",
      bg: "var(--ev-red-tint)",
      action: () => onNavigate?.("triage"),
    },
    {
      title: "Clinical Trials Radar",
      value: metrics.activeTrials || 310,
      sub: `${metrics.trialDiffs || 5} active change alerts`,
      icon: FlaskConical,
      accent: "var(--ev-dark-purple)",
      bg: "var(--ev-purple-50)",
      action: () => onNavigate?.("clinical-trials"),
    },
  ];

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto", overflowY: "auto" }}>
      {/* Top Banner */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 24,
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <h1 style={{ fontSize: "22px", fontWeight: 800, color: "var(--ev-dark-purple)", margin: 0, letterSpacing: "-0.3px" }}>
              CompetePulse Executive Dashboard
            </h1>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#107c41",
                backgroundColor: "#ecfdf5",
                border: "1px solid #a7f3d0",
                padding: "2px 8px",
                borderRadius: 12,
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <span style={{ width: 6, height: 6, borderRadius: "50%", backgroundColor: "#107c41" }} />
              Live Pipeline Synchronized
            </span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--ev-muted)", margin: 0 }}>
            Automated Competitive Intelligence for MASH/MASLD: Ingestion, URL Deduplication, Parallel AI Triage, and Executive Client Briefing.
          </p>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <button
            onClick={() => onNavigate?.("runner")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "9px 16px",
              borderRadius: 6,
              backgroundColor: "var(--ev-purple)",
              color: "#ffffff",
              border: "none",
              fontSize: "13px",
              fontWeight: 600,
              cursor: "pointer",
              boxShadow: "var(--ev-shadow-1)",
            }}
          >
            <Play size={15} fill="#ffffff" />
            Run Full Pipeline
          </button>
          <button
            onClick={() => onNavigate?.("morning-update")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "9px 16px",
              borderRadius: 6,
              backgroundColor: "#ffffff",
              color: "var(--ev-purple)",
              border: "1px solid var(--ev-outline)",
              fontSize: "13px",
              fontWeight: 600,
              cursor: "pointer",
              boxShadow: "var(--ev-shadow-1)",
            }}
          >
            <Mail size={15} />
            View Morning Brief
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 16,
          marginBottom: 24,
        }}
      >
        {kpis.map((kpi, idx) => {
          const Icon = kpi.icon;
          return (
            <div
              key={idx}
              onClick={kpi.action}
              style={{
                backgroundColor: "#ffffff",
                borderRadius: 8,
                padding: "20px 22px",
                border: "1px solid var(--ev-outline)",
                boxShadow: "var(--ev-shadow-1)",
                cursor: "pointer",
                transition: "all 0.15s ease",
                position: "relative",
                overflow: "hidden",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = "translateY(-2px)";
                e.currentTarget.style.boxShadow = "var(--ev-shadow-2)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = "translateY(0)";
                e.currentTarget.style.boxShadow = "var(--ev-shadow-1)";
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                <span style={{ fontSize: "12px", fontWeight: 700, color: "var(--ev-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                  {kpi.title}
                </span>
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 8,
                    backgroundColor: kpi.bg,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Icon size={18} color={kpi.accent} />
                </div>
              </div>
              <div style={{ fontSize: "28px", fontWeight: 800, color: "var(--ev-dark-purple)", marginBottom: 4, letterSpacing: "-0.5px" }}>
                {kpi.value}
              </div>
              <div style={{ fontSize: "12px", color: "var(--ev-subtle)", display: "flex", alignItems: "center", gap: 4 }}>
                <span>{kpi.sub}</span>
                <ChevronRight size={13} style={{ marginLeft: "auto", color: "var(--ev-subtle)" }} />
              </div>
            </div>
          );
        })}
      </div>

      {/* End-to-End Pipeline Stage Tracker */}
      <div
        style={{
          backgroundColor: "#ffffff",
          borderRadius: 8,
          padding: "22px 24px",
          border: "1px solid var(--ev-outline)",
          boxShadow: "var(--ev-shadow-1)",
          marginBottom: 24,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 18 }}>
          <div>
            <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--ev-dark-purple)", margin: "0 0 4px" }}>
              Automated 4-Stage CI Intelligence Workflow
            </h3>
            <p style={{ fontSize: "12.5px", color: "var(--ev-muted)", margin: 0 }}>
              Live execution chain connecting OPML feed collectors to client-ready intelligence briefing.
            </p>
          </div>
          <button
            onClick={() => onNavigate?.("runner")}
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--ev-blue)",
              backgroundColor: "transparent",
              border: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span>Orchestrator Logs</span>
            <ArrowRight size={13} />
          </button>
        </div>

        <PipelineStepper />
      </div>

      {/* Two Column Layout: Recent High-Impact Signals & CI Governance */}
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
        {/* Recent Signals Table */}
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            padding: "20px 22px",
            border: "1px solid var(--ev-outline)",
            boxShadow: "var(--ev-shadow-1)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div>
              <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--ev-dark-purple)", margin: "0 0 2px" }}>
                Recent Priority Signals
              </h3>
              <span style={{ fontSize: "12px", color: "var(--ev-muted)" }}>
                Latest items processed through Parallel AI screening
              </span>
            </div>
            <button
              onClick={() => onNavigate?.("triage")}
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: "var(--ev-purple)",
                backgroundColor: "transparent",
                border: "none",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span>View All ({metrics.triaged || 84})</span>
              <ChevronRight size={14} />
            </button>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {recentItems.map((item) => (
              <div
                key={item.id}
                onClick={() => setSelectedItem(item)}
                style={{
                  padding: "12px 14px",
                  borderRadius: 6,
                  border: "1px solid var(--ev-outline-soft)",
                  backgroundColor: "var(--ev-surface-2)",
                  cursor: "pointer",
                  transition: "all 0.12s ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--ev-purple-50)";
                  e.currentTarget.style.borderColor = "var(--ev-purple-200)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--ev-surface-2)";
                  e.currentTarget.style.borderColor = "var(--ev-outline-soft)";
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 6 }}>
                  <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--ev-dark-purple)", lineHeight: 1.35 }}>
                    {item.title}
                  </div>
                  <PriorityBadge priority={item.priority} />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: "11px", color: "var(--ev-muted)" }}>
                  <span>{item.source}</span>
                  <span>•</span>
                  <span>{item.published_date || item.publication_date || "Today"}</span>
                  <span style={{ marginLeft: "auto" }}>
                    <DispositionBadge disposition={item.disposition} />
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Intelligence Operations & Stakeholder Governance */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div
            style={{
              backgroundColor: "#ffffff",
              borderRadius: 8,
              padding: "20px 22px",
              border: "1px solid var(--ev-outline)",
              boxShadow: "var(--ev-shadow-1)",
            }}
          >
            <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--ev-dark-purple)", margin: "0 0 12px" }}>
              Stakeholder Engagement & Routing
            </h3>

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ padding: "10px 12px", borderRadius: 6, backgroundColor: "var(--ev-surface-2)", border: "1px solid var(--ev-outline-soft)" }}>
                <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--ev-subtle)", textTransform: "uppercase" }}>Primary Client Recipient</div>
                <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--ev-dark-purple)", marginTop: 2 }}>Dr. Elena Rostova</div>
                <div style={{ fontSize: "11.5px", color: "var(--ev-muted)" }}>Executive Director, Global CI (NovaBio Therapeutics)</div>
              </div>

              <div style={{ padding: "10px 12px", borderRadius: 6, backgroundColor: "var(--ev-surface-2)", border: "1px solid var(--ev-outline-soft)" }}>
                <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--ev-subtle)", textTransform: "uppercase" }}>Lead CI Strategist</div>
                <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--ev-dark-purple)", marginTop: 2 }}>Dr. Alexander Morgan</div>
                <div style={{ fontSize: "11.5px", color: "var(--ev-muted)" }}>Lead CI Strategist & Engagement Director, Evalueserve</div>
              </div>

              <div style={{ padding: "10px 12px", borderRadius: 6, backgroundColor: "var(--ev-surface-2)", border: "1px solid var(--ev-outline-soft)" }}>
                <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--ev-subtle)", textTransform: "uppercase" }}>Collaborating Reviewers</div>
                <div style={{ fontSize: "12px", color: "var(--ev-gray)", marginTop: 2 }}>
                  Jordan Vance, Taylor Hayes, Morgan Reed (CI Strategy Practice)
                </div>
              </div>
            </div>
          </div>

          {/* Quick Schedule Status Card */}
          <div
            style={{
              backgroundColor: "var(--ev-purple-50)",
              borderRadius: 8,
              padding: "18px 20px",
              border: "1px solid var(--ev-purple-200)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <Clock size={16} color="var(--ev-purple)" />
              <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--ev-purple)" }}>
                Next Automated Cycle
              </span>
            </div>
            <p style={{ fontSize: "12.5px", color: "var(--ev-dark-purple)", margin: "0 0 10px", lineHeight: 1.5 }}>
              Evening cycle scheduled at <strong>18:00 IST</strong> covering overnight Western markets and regulatory filings.
            </p>
            <button
              onClick={() => onNavigate?.("settings")}
              style={{
                fontSize: "11.5px",
                fontWeight: 700,
                color: "var(--ev-purple)",
                backgroundColor: "transparent",
                border: "none",
                cursor: "pointer",
                padding: 0,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span>Manage Mail & Threshold Settings</span>
              <ChevronRight size={12} />
            </button>
          </div>
        </div>
      </div>

      {/* Slide-out Article Detail Drawer */}
      <DetailDrawer
        item={selectedItem}
        onClose={() => setSelectedItem(null)}
        onUpdateDisposition={async (id, patch) => {
          await api.updateArticle(id, patch);
          setSelectedItem((prev) => (prev ? { ...prev, ...patch } : null));
        }}
      />
    </div>
  );
}
