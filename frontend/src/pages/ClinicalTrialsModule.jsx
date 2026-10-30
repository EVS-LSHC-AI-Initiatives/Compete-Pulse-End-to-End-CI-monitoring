import React, { useState, useEffect } from "react";
import {
  FlaskConical,
  Search,
  Filter,
  ArrowRight,
  ExternalLink,
  Layers,
  FileSpreadsheet,
  AlertCircle,
  CheckCircle2,
  Calendar,
  Building2,
  ChevronDown,
  Sparkles,
  Download,
  X
} from "lucide-react";
import { C, SHADOWS, RADIUS } from "../theme.js";
import { api } from "../api.js";
import { PageLoader } from "../components/loaders/index.jsx";

export default function ClinicalTrialsModule() {
  const [activeTab, setActiveTab] = useState("diffs"); // 'diffs', 'trials', 'queries'
  const [trials, setTrials] = useState([]);
  const [queries, setQueries] = useState([]);
  const [changes, setChanges] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [phaseFilter, setPhaseFilter] = useState("All");

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      try {
        const [t, q, c] = await Promise.all([
          api.getTrials({ search, phase: phaseFilter }),
          api.getQueries(search),
          api.getTrialChanges(),
        ]);
        setTrials(Array.isArray(t) ? t : (t?.trials || []));
        setQueries(Array.isArray(q) ? q : (q?.queries || []));
        setChanges(Array.isArray(c) ? c : (c?.diffs || []));
      } catch (err) {
        console.error("Failed to load CT data", err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [search, phaseFilter]);

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto", overflowY: "auto" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, flexWrap: "wrap", gap: 14 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <h1 style={{ fontSize: "22px", fontWeight: 800, color: "var(--ev-dark-purple)", margin: 0, letterSpacing: "-0.3px" }}>
              ClinicalTrials.gov Intelligence & Change Detection
            </h1>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#92400e",
                backgroundColor: "#fef3c7",
                border: "1px solid #fde68a",
                padding: "2px 8px",
                borderRadius: 12,
              }}
            >
              310 Monitored Programs
            </span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--ev-muted)", margin: 0 }}>
            Automated registry diffs comparing current trials against baseline workbooks with color-coded field alterations.
          </p>
        </div>

        <button
          onClick={() => {
            const csv = changes.map((c) => `"${c.nct_id}","${c.trial_title}","${c.field_name}","${c.old_value}","${c.new_value}"`).join("\n");
            const blob = new Blob([`NCT_ID,Title,Field,Old_Value,New_Value\n${csv}`], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `CompetePulse_CT_Diffs_${new Date().toISOString().slice(0, 10)}.csv`;
            a.click();
          }}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 16px",
            borderRadius: 6,
            border: "1px solid var(--ev-outline)",
            backgroundColor: "#ffffff",
            color: "var(--ev-dark-purple)",
            fontSize: "12.5px",
            fontWeight: 600,
            cursor: "pointer",
            boxShadow: "var(--ev-shadow-1)",
          }}
        >
          <Download size={14} />
          <span>Export Diffs CSV</span>
        </button>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--ev-outline)", marginBottom: 16 }}>
        <button
          onClick={() => setActiveTab("diffs")}
          style={{
            padding: "10px 18px",
            border: "none",
            borderBottom: activeTab === "diffs" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            color: activeTab === "diffs" ? "var(--ev-purple)" : "var(--ev-muted)",
            fontWeight: 700,
            fontSize: "13px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          Change Detection Diffs
          <span style={{ fontSize: "11px", backgroundColor: "#fef3c7", color: "#92400e", padding: "1px 7px", borderRadius: 10, fontWeight: 700 }}>
            {changes.length} Alerts
          </span>
        </button>
        <button
          onClick={() => setActiveTab("trials")}
          style={{
            padding: "10px 18px",
            border: "none",
            borderBottom: activeTab === "trials" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            color: activeTab === "trials" ? "var(--ev-purple)" : "var(--ev-muted)",
            fontWeight: 700,
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          Enriched Clinical Studies ({trials.length})
        </button>
        <button
          onClick={() => setActiveTab("queries")}
          style={{
            padding: "10px 18px",
            border: "none",
            borderBottom: activeTab === "queries" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            color: activeTab === "queries" ? "var(--ev-purple)" : "var(--ev-muted)",
            fontWeight: 700,
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          Search Query Batch ({queries.length || 310})
        </button>
      </div>

      {/* Search Toolbar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          backgroundColor: "#ffffff",
          padding: "12px 18px",
          borderRadius: 8,
          border: "1px solid var(--ev-outline)",
          marginBottom: 16,
          boxShadow: "var(--ev-shadow-1)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, width: 360, backgroundColor: "var(--ev-surface-2)", padding: "6px 12px", borderRadius: 6, border: "1px solid var(--ev-outline)" }}>
          <Search size={15} color="var(--ev-subtle)" />
          <input
            type="text"
            placeholder="Search NCT ID, drug program, sponsor..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ border: "none", background: "transparent", outline: "none", fontSize: "12.5px", width: "100%", color: "var(--ev-gray)" }}
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--ev-subtle)" }}
            >
              <X size={13} />
            </button>
          )}
        </div>

        {activeTab === "trials" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "12px" }}>
            <span style={{ fontWeight: 600, color: "var(--ev-muted)" }}>Phase:</span>
            <select
              value={phaseFilter}
              onChange={(e) => setPhaseFilter(e.target.value)}
              style={{ padding: "6px 10px", borderRadius: 4, border: "1px solid var(--ev-outline)", backgroundColor: "#ffffff", fontSize: "12px", color: "var(--ev-gray)" }}
            >
              <option value="All">All Phases</option>
              <option value="Phase 1">Phase 1</option>
              <option value="Phase 2">Phase 2</option>
              <option value="Phase 3">Phase 3</option>
            </select>
          </div>
        )}
      </div>

      {/* Main View Tab Content */}
      {loading ? (
        <PageLoader text="Inspecting ClinicalTrials registry..." />
      ) : activeTab === "diffs" ? (
        /* Diffs View */
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {changes.map((diff, idx) => (
            <div
              key={idx}
              style={{
                backgroundColor: "#ffffff",
                borderRadius: 8,
                padding: "18px 22px",
                border: "1px solid var(--ev-outline)",
                boxShadow: "var(--ev-shadow-1)",
                borderLeft: "4px solid #d97706",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{ fontFamily: "'DM Mono', monospace", fontSize: "12px", fontWeight: 700, color: "var(--ev-purple)", backgroundColor: "var(--ev-purple-50)", padding: "2px 7px", borderRadius: 4 }}>
                      {diff.nct_id}
                    </span>
                    <strong style={{ fontSize: "14px", color: "var(--ev-dark-purple)" }}>{diff.trial_title}</strong>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--ev-muted)" }}>{diff.sponsor}</div>
                </div>

                <a
                  href={`https://clinicaltrials.gov/study/${diff.nct_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: "12px", color: "var(--ev-blue)", fontWeight: 600, textDecoration: "none" }}
                >
                  <span>CT.gov Registry</span>
                  <ExternalLink size={12} />
                </a>
              </div>

              {/* Diff values table */}
              <div style={{ display: "grid", gridTemplateColumns: "180px 1fr 1fr", gap: 12, backgroundColor: "var(--ev-surface-2)", padding: "12px 14px", borderRadius: 6, fontSize: "12px", marginTop: 10 }}>
                <div>
                  <span style={{ color: "var(--ev-subtle)", fontWeight: 600 }}>Modified Field:</span>
                  <div style={{ fontWeight: 700, color: "var(--ev-dark-purple)", marginTop: 2 }}>{diff.field_name}</div>
                </div>
                <div>
                  <span style={{ color: "#991b1b", fontWeight: 600 }}>Previous Value:</span>
                  <div style={{ color: "#991b1b", textDecoration: "line-through", marginTop: 2 }}>{diff.old_value}</div>
                </div>
                <div>
                  <span style={{ color: "#065f46", fontWeight: 600 }}>Detected Change (Current):</span>
                  <div style={{ fontWeight: 700, color: "#065f46", backgroundColor: "#ecfdf5", padding: "2px 6px", borderRadius: 4, display: "inline-block", marginTop: 2 }}>
                    {diff.new_value}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : activeTab === "trials" ? (
        /* Enriched Studies View */
        <div style={{ backgroundColor: "#ffffff", borderRadius: 8, border: "1px solid var(--ev-outline)", boxShadow: "var(--ev-shadow-1)", overflow: "hidden" }}>
          <table style={{ width: "100%", fontSize: "12.5px", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ backgroundColor: "var(--ev-surface-3)", borderBottom: "1px solid var(--ev-outline)", textAlign: "left" }}>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 110 }}>NCT ID</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)" }}>Title & Drug Program</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 140 }}>Sponsor</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 90 }}>Phase</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 130 }}>Status</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 100 }}>Enrollment</th>
              </tr>
            </thead>
            <tbody>
              {trials.map((trial) => (
                <tr
                  key={trial.nct_id}
                  style={{ borderBottom: "1px solid var(--ev-outline-soft)", transition: "background 0.1s ease" }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-purple-50)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                >
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <a
                      href={`https://clinicaltrials.gov/study/${trial.nct_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ fontFamily: "'DM Mono', monospace", fontWeight: 700, color: "var(--ev-blue)", textDecoration: "none" }}
                    >
                      {trial.nct_id}
                    </a>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <div style={{ fontWeight: 700, color: "var(--ev-dark-purple)", marginBottom: 3 }}>{trial.title}</div>
                    <div style={{ fontSize: "11.5px", color: "var(--ev-muted)" }}>Drug: {trial.drug}</div>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top", color: "var(--ev-gray)" }}>{trial.sponsor}</td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <span style={{ padding: "2px 6px", borderRadius: 4, backgroundColor: "var(--ev-surface-2)", fontSize: "11px", fontWeight: 600, color: "var(--ev-purple)" }}>
                      {trial.phase}
                    </span>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top" }}>
                    <span style={{ fontSize: "11px", fontWeight: 600, color: trial.status?.includes("Recruiting") ? "#065f46" : "var(--ev-gray)" }}>
                      {trial.status}
                    </span>
                  </td>
                  <td style={{ padding: "12px 14px", verticalAlign: "top", color: "var(--ev-muted)" }}>{trial.enrollment || "N/A"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        /* Queries View */
        <div style={{ backgroundColor: "#ffffff", borderRadius: 8, border: "1px solid var(--ev-outline)", boxShadow: "var(--ev-shadow-1)", overflow: "hidden" }}>
          <table style={{ width: "100%", fontSize: "12.5px", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ backgroundColor: "var(--ev-surface-3)", borderBottom: "1px solid var(--ev-outline)", textAlign: "left" }}>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 60 }}>#</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)" }}>Search Query Program</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 140 }}>Category</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 140 }}>Target Tag</th>
              </tr>
            </thead>
            <tbody>
              {queries.map((q, idx) => (
                <tr
                  key={idx}
                  style={{ borderBottom: "1px solid var(--ev-outline-soft)", transition: "background 0.1s ease" }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--ev-purple-50)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                >
                  <td style={{ padding: "10px 14px", color: "var(--ev-subtle)" }}>{idx + 1}</td>
                  <td style={{ padding: "10px 14px", fontWeight: 600, color: "var(--ev-dark-purple)" }}>{q.query}</td>
                  <td style={{ padding: "10px 14px", color: "var(--ev-muted)" }}>{q.category || "MASH Target"}</td>
                  <td style={{ padding: "10px 14px" }}>
                    <span style={{ fontSize: "11px", padding: "2px 7px", borderRadius: 4, backgroundColor: "var(--ev-surface-2)", color: "var(--ev-purple)" }}>
                      {q.tag || "Core"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
