import React, { useState, useEffect } from "react";
import {
  Search,
  Filter,
  Columns,
  Table as TableIcon,
  Sparkles,
  Download,
  ExternalLink,
  ShieldCheck,
  Building2,
  Calendar,
  CheckCircle,
  Eye,
  X,
  SlidersHorizontal,
  Bookmark,
  Trash2,
  Check,
  Tag
} from "lucide-react";
import { C, SHADOWS, RADIUS } from "../theme.js";
import { api } from "../api.js";
import { PageLoader } from "../components/loaders/index.jsx";
import { PriorityBadge, DispositionBadge, ScopeBadge } from "../components/StatusBadge.jsx";
import DetailDrawer from "../components/DetailDrawer.jsx";
import PipelineStepper from "../components/PipelineStepper.jsx";

const PRIORITIES = ["All", "High", "Medium", "Low"];
const DISPOSITIONS = ["All", "Approved for Morning Brief", "Kept for Watchlist", "Rejected", "Pending Review"];
const SCOPES = ["All", "In Scope", "Out of Scope"];
const CATEGORIES = ["All", "Company Alert", "Regulatory", "Clinical Trials", "Google Alert", "Media Aggregator"];

export default function NewsTriageModule() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState("table"); // 'table' or 'kanban'
  const [selectedItem, setSelectedItem] = useState(null);

  // Filters
  const [search, setSearch] = useState("");
  const [priority, setPriority] = useState("All");
  const [disposition, setDisposition] = useState("All");
  const [scope, setScope] = useState("All");
  const [category, setCategory] = useState("All");

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.getTriagedItems({ search, priority, disposition, scope, category });
      const list = Array.isArray(res) ? res : (res?.items || []);
      setItems(list);
    } catch (err) {
      console.error("Failed to load triaged items", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [search, priority, disposition, scope, category]);

  const handleUpdateArticle = async (id, patch) => {
    await api.updateArticle(id, patch);
    setItems((prev) =>
      prev.map((it) => (it.id === id ? { ...it, ...patch } : it))
    );
    if (selectedItem && selectedItem.id === id) {
      setSelectedItem((prev) => ({ ...prev, ...patch }));
    }
  };

  const highCount = items.filter((it) => it.priority === "High").length;
  const inScopeCount = items.filter((it) => (it.mash_scope || "").toLowerCase().includes("in scope")).length;

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto", overflowY: "auto" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, flexWrap: "wrap", gap: 14 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <h1 style={{ fontSize: "22px", fontWeight: 800, color: "var(--ev-dark-purple)", margin: 0, letterSpacing: "-0.3px" }}>
              News Intelligence & Parallel AI Triage
            </h1>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "var(--ev-purple)",
                backgroundColor: "var(--ev-purple-50)",
                border: "1px solid var(--ev-purple-200)",
                padding: "2px 8px",
                borderRadius: 12,
              }}
            >
              Stage 3 · Screening Active
            </span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--ev-muted)", margin: 0 }}>
            Triaged articles evaluated for MASH therapeutic scope, clinical impact, and inclusion in the client update brief.
          </p>
        </div>

        {/* View Switcher & Export */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              display: "flex",
              backgroundColor: "var(--ev-surface-2)",
              borderRadius: 6,
              border: "1px solid var(--ev-outline)",
              padding: 2,
            }}
          >
            <button
              onClick={() => setViewMode("table")}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "6px 12px",
                borderRadius: 4,
                border: "none",
                backgroundColor: viewMode === "table" ? "#ffffff" : "transparent",
                color: viewMode === "table" ? "var(--ev-dark-purple)" : "var(--ev-muted)",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
                boxShadow: viewMode === "table" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}
            >
              <TableIcon size={14} />
              <span>Table</span>
            </button>
            <button
              onClick={() => setViewMode("kanban")}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "6px 12px",
                borderRadius: 4,
                border: "none",
                backgroundColor: viewMode === "kanban" ? "#ffffff" : "transparent",
                color: viewMode === "kanban" ? "var(--ev-dark-purple)" : "var(--ev-muted)",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
                boxShadow: viewMode === "kanban" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}
            >
              <Columns size={14} />
              <span>Kanban</span>
            </button>
          </div>

          <button
            onClick={() => {
              const csv = items.map((it) => `"${it.id}","${it.title}","${it.priority}","${it.disposition}"`).join("\n");
              const blob = new Blob([`ID,Title,Priority,Disposition\n${csv}`], { type: "text/csv" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `CompetePulse_Triaged_News_${new Date().toISOString().slice(0, 10)}.csv`;
              a.click();
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "7px 14px",
              borderRadius: 6,
              border: "1px solid var(--ev-outline)",
              backgroundColor: "var(--ev-white)",
              color: "var(--ev-dark-purple)",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
              boxShadow: "var(--ev-shadow-1)",
            }}
          >
            <Download size={13} />
            <span>Export CSV</span>
          </button>
        </div>
      </div>

      {/* Pipeline Stepper Compact */}
      <div style={{ marginBottom: 20 }}>
        <PipelineStepper currentStage={3} onStageClick={() => {}} />
      </div>

      {/* Filters Toolbar */}
      <div
        style={{
          backgroundColor: "#ffffff",
          borderRadius: 8,
          padding: "16px 20px",
          border: "1px solid var(--ev-outline)",
          boxShadow: "var(--ev-shadow-1)",
          marginBottom: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {/* Search box with instant clear */}
          <div style={{ position: "relative", flex: "1 1 280px" }}>
            <Search size={15} color="var(--ev-subtle)" style={{ position: "absolute", left: 11, top: 10 }} />
            <input
              type="text"
              placeholder="Search by keyword, drug target, company, or ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                width: "100%",
                padding: "8px 30px 8px 32px",
                borderRadius: 6,
                border: "1px solid var(--ev-outline-strong)",
                fontSize: "12.5px",
                fontFamily: "'Inter', sans-serif",
              }}
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                style={{
                  position: "absolute",
                  right: 8,
                  top: 8,
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--ev-subtle)",
                }}
              >
                <X size={14} />
              </button>
            )}
          </div>

          {/* Priority filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ev-muted)" }}>Priority:</span>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              style={{
                padding: "7px 10px",
                borderRadius: 6,
                border: "1px solid var(--ev-outline-strong)",
                fontSize: "12px",
                backgroundColor: "#ffffff",
                color: "var(--ev-gray)",
                fontWeight: 500,
              }}
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>

          {/* Scope filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ev-muted)" }}>Scope:</span>
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              style={{
                padding: "7px 10px",
                borderRadius: 6,
                border: "1px solid var(--ev-outline-strong)",
                fontSize: "12px",
                backgroundColor: "#ffffff",
                color: "var(--ev-gray)",
                fontWeight: 500,
              }}
            >
              {SCOPES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {/* Category filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ev-muted)" }}>Feed Category:</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              style={{
                padding: "7px 10px",
                borderRadius: 6,
                border: "1px solid var(--ev-outline-strong)",
                fontSize: "12px",
                backgroundColor: "#ffffff",
                color: "var(--ev-gray)",
                fontWeight: 500,
              }}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* Reset Filters */}
          {(search || priority !== "All" || scope !== "All" || category !== "All" || disposition !== "All") && (
            <button
              onClick={() => {
                setSearch("");
                setPriority("All");
                setScope("All");
                setCategory("All");
                setDisposition("All");
              }}
              style={{
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid var(--ev-outline)",
                backgroundColor: "var(--ev-surface-2)",
                color: "var(--ev-purple)",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Reset Filters
            </button>
          )}
        </div>

        {/* Quick summary counts */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: "12px", color: "var(--ev-muted)", borderTop: "1px solid var(--ev-outline-soft)", paddingTop: 10 }}>
          <span>Showing <strong>{items.length}</strong> matching intelligence candidates</span>
          <span>•</span>
          <span style={{ color: "var(--ev-red-shade)", fontWeight: 600 }}>{highCount} High Priority</span>
          <span>•</span>
          <span style={{ color: "#065f46", fontWeight: 600 }}>{inScopeCount} In Therapeutic Scope</span>
        </div>
      </div>

      {/* Main View: Table or Kanban */}
      {loading ? (
        <PageLoader text="Filtering CI Intelligence candidates..." />
      ) : viewMode === "table" ? (
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            border: "1px solid var(--ev-outline)",
            boxShadow: "var(--ev-shadow-1)",
            overflow: "hidden",
          }}
        >
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "12.5px" }}>
            <thead>
              <tr style={{ backgroundColor: "var(--ev-surface-3)", borderBottom: "1px solid var(--ev-outline)" }}>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 90 }}>ID</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)" }}>Article & Takeaway</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 140 }}>Source</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 100 }}>Priority</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 110 }}>Scope</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 140 }}>Disposition</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 80, textAlign: "center" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => setSelectedItem(item)}
                  style={{
                    borderBottom: "1px solid var(--ev-outline-soft)",
                    cursor: "pointer",
                    transition: "background 0.1s ease",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-purple-50)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                >
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <span style={{ fontFamily: "'DM Mono', monospace", fontSize: "11px", fontWeight: 700, color: "var(--ev-purple)" }}>
                      {item.id}
                    </span>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <div style={{ fontWeight: 700, color: "var(--ev-dark-purple)", lineHeight: 1.4, marginBottom: 4 }}>
                      {item.title}
                    </div>
                    <div style={{ fontSize: "12px", color: "var(--ev-muted)", lineHeight: 1.5 }}>
                      {item.takeaway || item.summary}
                    </div>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <div style={{ fontWeight: 600, color: "var(--ev-gray)" }}>{item.source}</div>
                    <div style={{ fontSize: "11px", color: "var(--ev-subtle)" }}>{item.published_date || item.publication_date || "Today"}</div>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <PriorityBadge priority={item.priority} />
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <ScopeBadge scope={item.mash_scope} />
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <DispositionBadge disposition={item.disposition} />
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top", textAlign: "center" }}>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedItem(item);
                      }}
                      title="Inspect details & change disposition"
                      style={{
                        padding: "4px 8px",
                        borderRadius: 4,
                        border: "1px solid var(--ev-outline)",
                        backgroundColor: "#ffffff",
                        color: "var(--ev-purple)",
                        cursor: "pointer",
                      }}
                    >
                      <Eye size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        /* Kanban View */
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
          {[
            { key: "Approved for Morning Brief", label: "Approved for Brief", color: "var(--ev-red)" },
            { key: "Kept for Watchlist", label: "Watchlist / Tracker", color: "var(--ev-blue)" },
            { key: "Pending Review", label: "Pending Review", color: "var(--ev-purple)" },
          ].map((col) => {
            const colItems = items.filter((it) => {
              if (col.key === "Pending Review") {
                return it.disposition !== "Approved for Morning Brief" && it.disposition !== "Kept for Watchlist" && it.disposition !== "Rejected";
              }
              return it.disposition === col.key;
            });

            return (
              <div
                key={col.key}
                style={{
                  backgroundColor: "var(--ev-surface-2)",
                  borderRadius: 8,
                  border: "1px solid var(--ev-outline)",
                  padding: "16px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 12,
                  minHeight: 500,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: `2px solid ${col.color}`, paddingBottom: 8 }}>
                  <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--ev-dark-purple)" }}>{col.label}</span>
                  <span style={{ fontSize: "11px", fontWeight: 700, backgroundColor: "#ffffff", padding: "2px 7px", borderRadius: 10, border: "1px solid var(--ev-outline)" }}>
                    {colItems.length}
                  </span>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 10, overflowY: "auto" }}>
                  {colItems.map((item) => (
                    <div
                      key={item.id}
                      onClick={() => setSelectedItem(item)}
                      style={{
                        backgroundColor: "#ffffff",
                        borderRadius: 6,
                        padding: "12px",
                        border: "1px solid var(--ev-outline-soft)",
                        cursor: "pointer",
                        boxShadow: "var(--ev-shadow-1)",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                        <span style={{ fontSize: "10px", fontWeight: 700, color: "var(--ev-purple)" }}>{item.id}</span>
                        <PriorityBadge priority={item.priority} />
                      </div>
                      <div style={{ fontSize: "12.5px", fontWeight: 700, color: "var(--ev-dark-purple)", lineHeight: 1.35, marginBottom: 6 }}>
                        {item.title}
                      </div>
                      <div style={{ fontSize: "11px", color: "var(--ev-subtle)", display: "flex", justifyContent: "space-between" }}>
                        <span>{item.source}</span>
                        <span>{item.published_date || "Today"}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Slide-out Article Detail Drawer */}
      <DetailDrawer
        item={selectedItem}
        onClose={() => setSelectedItem(null)}
        onUpdateDisposition={handleUpdateArticle}
      />
    </div>
  );
}
