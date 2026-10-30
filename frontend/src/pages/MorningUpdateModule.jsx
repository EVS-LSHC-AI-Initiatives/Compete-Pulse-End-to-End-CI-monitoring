import React, { useState, useEffect } from "react";
import {
  Mail,
  Copy,
  Check,
  Download,
  ExternalLink,
  ShieldCheck,
  RefreshCw,
  FileText,
  User,
  Sparkles,
  Info,
  SlidersHorizontal,
  Send
} from "lucide-react";
import { C, SHADOWS, RADIUS } from "../theme.js";
import { api } from "../api.js";
import { PageLoader } from "../components/loaders/index.jsx";

export default function MorningUpdateModule() {
  const [draft, setDraft] = useState(null);
  const [auditLog, setAuditLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [slot, setSlot] = useState("Morning");
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState("preview"); // 'preview' or 'audit'

  useEffect(() => {
    async function loadDraft() {
      setLoading(true);
      try {
        const d = await api.getMorningDraft(slot);
        const log = await api.getSelectionLog();
        setDraft(d);
        setAuditLog(log);
      } catch (err) {
        console.error("Failed to load morning draft", err);
      } finally {
        setLoading(false);
      }
    }
    loadDraft();
  }, [slot]);

  // Generates clean HTML suitable for Outlook email body
  const generateOutlookHtml = () => {
    if (!draft) return "";

    let html = `
<div style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #1f2937; line-height: 1.45;">
  <p>Hi ${draft.greeting_name || "Elena"},</p>
  <p>${slot} monitoring has been completed, and the identified updates are summarized below.</p>
`;

    draft.sections.forEach((sec) => {
      html += `<h3 style="color: #43224d; font-size: 13pt; margin-top: 24px; margin-bottom: 8px; border-bottom: 1.5px solid #43224d; padding-bottom: 4px;">${sec.section_title}</h3>`;
      if (sec.disclaimer) {
        html += `<p style="font-size: 9.5pt; color: #6b7280; font-style: italic;">${sec.disclaimer}</p>`;
      }

      sec.items.forEach((item) => {
        html += `
<div style="margin-bottom: 18px;">
  <p style="margin: 0 0 4px 0;">
    <strong style="color: #111827; font-size: 11.5pt;">${item.headline}</strong>
    <br/>
    <em style="color: #4b5563; font-size: 10pt;">${item.source_label}</em>
    ${item.circulation_tag ? `&nbsp;<span style="color: #15803d; font-weight: bold; font-size: 9pt;">[${item.circulation_tag}]</span>` : ""}
  </p>
  <ul style="margin: 4px 0 8px 24px; padding: 0; color: #374151;">
`;
        item.bullets.forEach((b) => {
          html += `<li style="margin-bottom: 3px;">${b}</li>`;
        });
        html += `</ul>`;

        if (item.evalueserve_suggestion) {
          html += `
  <div style="margin-left: 36px; padding: 6px 12px; background-color: #f5f2f6; border-left: 3px solid #43224d; font-size: 10pt; color: #301038;">
    <strong style="color: #43224d;">Evalueserve Suggestion: </strong>${item.evalueserve_suggestion.replace("Recommendation: ", "")}
  </div>`;
        }
        html += `</div>`;
      });
    });

    if (draft.consultant) {
      html += `
  <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 10pt; color: #4b5563;">
    <p style="margin: 0 0 4px 0;">${draft.consultant.closing}</p>
    <p style="margin: 0; font-weight: bold; color: #43224d; font-size: 11pt;">${draft.consultant.name}</p>
    <p style="margin: 0;">${draft.consultant.title}</p>
    <p style="margin: 0;">${draft.consultant.org}</p>
    <p style="margin: 4px 0 0 0;">${draft.consultant.phone}</p>
  </div>`;
    }

    html += `</div>`;
    return html;
  };

  const copyForOutlook = async () => {
    const html = generateOutlookHtml();
    try {
      const blob = new Blob([html], { type: "text/html" });
      const textBlob = new Blob([html.replace(/<[^>]+>/g, "")], { type: "text/plain" });
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": blob,
          "text/plain": textBlob,
        }),
      ]);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch (err) {
      navigator.clipboard.writeText(html);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    }
  };

  if (loading || !draft) {
    return <PageLoader text="Assembling Morning Update Brief..." />;
  }

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1200, margin: "0 auto", overflowY: "auto" }}>
      {/* Header & Controls */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 20,
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <h1 style={{ fontSize: "22px", fontWeight: 800, color: "var(--ev-dark-purple)", margin: 0, letterSpacing: "-0.3px" }}>
              Client Update Briefing Studio
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
              Stage 4 · Pre-Rendered HTML
            </span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--ev-muted)", margin: 0 }}>
            Curated intelligence formatted for executive circulation, featuring primary sources and Evalueserve advisory callouts.
          </p>
        </div>

        {/* Slot Toggle & Actions */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
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
              onClick={() => setSlot("Morning")}
              style={{
                padding: "6px 14px",
                borderRadius: 4,
                border: "none",
                backgroundColor: slot === "Morning" ? "#ffffff" : "transparent",
                color: slot === "Morning" ? "var(--ev-purple)" : "var(--ev-muted)",
                fontSize: "12px",
                fontWeight: 700,
                cursor: "pointer",
                boxShadow: slot === "Morning" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}
            >
              Morning Slot
            </button>
            <button
              onClick={() => setSlot("Evening")}
              style={{
                padding: "6px 14px",
                borderRadius: 4,
                border: "none",
                backgroundColor: slot === "Evening" ? "#ffffff" : "transparent",
                color: slot === "Evening" ? "var(--ev-purple)" : "var(--ev-muted)",
                fontSize: "12px",
                fontWeight: 700,
                cursor: "pointer",
                boxShadow: slot === "Evening" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}
            >
              Evening Slot
            </button>
          </div>

          <button
            onClick={copyForOutlook}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 18px",
              borderRadius: 6,
              backgroundColor: copied ? "#107c41" : "var(--ev-purple)",
              color: "#ffffff",
              border: "none",
              fontSize: "13px",
              fontWeight: 700,
              cursor: "pointer",
              boxShadow: "var(--ev-shadow-1)",
              transition: "background-color 0.15s ease",
            }}
          >
            {copied ? <Check size={15} /> : <Copy size={15} />}
            <span>{copied ? "Copied Formatted HTML!" : "Copy for Outlook"}</span>
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--ev-outline)", marginBottom: 20 }}>
        <button
          onClick={() => setActiveTab("preview")}
          style={{
            padding: "10px 18px",
            border: "none",
            borderBottom: activeTab === "preview" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            color: activeTab === "preview" ? "var(--ev-purple)" : "var(--ev-muted)",
            fontWeight: 700,
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          Formatted Brief Preview
        </button>
        <button
          onClick={() => setActiveTab("audit")}
          style={{
            padding: "10px 18px",
            border: "none",
            borderBottom: activeTab === "audit" ? "2px solid var(--ev-purple)" : "2px solid transparent",
            backgroundColor: "transparent",
            color: activeTab === "audit" ? "var(--ev-purple)" : "var(--ev-muted)",
            fontWeight: 700,
            fontSize: "13px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          Selection & Provenance Pass Log
          <span style={{ fontSize: "11px", backgroundColor: "var(--ev-surface-3)", padding: "1px 6px", borderRadius: 10, color: "var(--ev-subtle)" }}>
            {auditLog.length}
          </span>
        </button>
      </div>

      {activeTab === "preview" ? (
        /* Email Container Card */
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            border: "1px solid var(--ev-outline)",
            boxShadow: "var(--ev-shadow-1)",
            padding: "36px 44px",
            maxWidth: 960,
            margin: "0 auto",
            fontSize: "14px",
            color: "var(--ev-dark-purple)",
            lineHeight: 1.55,
          }}
        >
          {/* Header Metadata */}
          <div style={{ paddingBottom: 16, borderBottom: "1px solid var(--ev-outline-soft)", marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontSize: "16px", fontWeight: 800, color: "var(--ev-purple)" }}>
                {slot} Update: {draft.date}
              </span>
              <span style={{ fontSize: "12px", color: "var(--ev-subtle)", display: "flex", alignItems: "center", gap: 5 }}>
                <ShieldCheck size={14} color="#107c41" /> Provenance Verified
              </span>
            </div>
            <div style={{ fontSize: "12px", color: "var(--ev-muted)" }}>
              <strong>To:</strong> Dr. Elena Rostova (NovaBio Therapeutics) · <strong>CC:</strong> Jordan Vance, Taylor Hayes, Morgan Reed
            </div>
          </div>

          {/* Salutation & Intro */}
          <p style={{ margin: "0 0 12px 0" }}>Hi {draft.greeting_name || "Elena"},</p>
          <p style={{ margin: "0 0 24px 0", color: "var(--ev-gray)" }}>
            {slot} monitoring has been completed, and the identified updates are summarized below.
          </p>

          {/* Sections */}
          {draft.sections.map((section, sIdx) => (
            <div key={sIdx} style={{ marginBottom: 32 }}>
              <div
                style={{
                  fontSize: "14.5px",
                  fontWeight: 800,
                  color: "var(--ev-purple)",
                  borderBottom: "2px solid var(--ev-purple)",
                  paddingBottom: 4,
                  marginBottom: 16,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                }}
              >
                {section.section_title}
              </div>

              {section.disclaimer && (
                <div style={{ fontSize: "12px", color: "var(--ev-subtle)", fontStyle: "italic", marginBottom: 16 }}>
                  {section.disclaimer}
                </div>
              )}

              {section.items.length === 0 ? (
                <div style={{ padding: "16px", backgroundColor: "var(--ev-surface-2)", borderRadius: 6, color: "var(--ev-muted)", fontStyle: "italic", fontSize: "13px" }}>
                  No updates identified for this section during this cycle.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
                  {section.items.map((item, iIdx) => (
                    <div key={iIdx} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {/* Story Headline & Source Tag */}
                      <div>
                        <div style={{ fontSize: "14.5px", fontWeight: 700, color: "var(--ev-dark-purple)", lineHeight: 1.35 }}>
                          {item.headline}
                        </div>
                        <div style={{ fontSize: "12px", color: "var(--ev-muted)", marginTop: 2 }}>
                          <em>{item.source_label}</em>
                          {item.circulation_tag && (
                            <span style={{ color: "#15803d", fontWeight: 700, marginLeft: 8 }}>
                              [{item.circulation_tag}]
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Story Bullets */}
                      <ul style={{ margin: "0 0 0 20px", padding: 0, color: "var(--ev-gray)", fontSize: "13.5px" }}>
                        {item.bullets.map((bullet, bIdx) => (
                          <li key={bIdx} style={{ marginBottom: 4 }}>
                            {bullet}
                          </li>
                        ))}
                      </ul>

                      {/* Evalueserve Suggestion Note */}
                      {item.evalueserve_suggestion && (
                        <div
                          style={{
                            marginLeft: 36,
                            padding: "8px 14px",
                            backgroundColor: "var(--ev-purple-50)",
                            borderLeft: "3px solid var(--ev-purple)",
                            borderRadius: "0 6px 6px 0",
                            fontSize: "12.5px",
                            color: "var(--ev-dark-purple)",
                          }}
                        >
                          <strong style={{ color: "var(--ev-purple)" }}>Evalueserve Suggestion: </strong>
                          {item.evalueserve_suggestion.replace("Recommendation: ", "")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

          {/* Signature */}
          {draft.consultant && (
            <div style={{ borderTop: "1px solid var(--ev-outline-soft)", paddingTop: 20, marginTop: 32, fontSize: "13px" }}>
              <div style={{ marginBottom: 4, color: "var(--ev-gray)" }}>{draft.consultant.closing}</div>
              <div style={{ fontWeight: 800, color: "var(--ev-purple)", fontSize: "14px" }}>{draft.consultant.name}</div>
              <div style={{ color: "var(--ev-muted)" }}>{draft.consultant.title}</div>
              <div style={{ color: "var(--ev-muted)" }}>{draft.consultant.org}</div>
              <div style={{ color: "var(--ev-subtle)", marginTop: 4 }}>{draft.consultant.phone}</div>
            </div>
          )}
        </div>
      ) : (
        /* Provenance & Selection Log Table */
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            border: "1px solid var(--ev-outline)",
            boxShadow: "var(--ev-shadow-1)",
            overflow: "hidden",
          }}
        >
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--ev-outline-soft)", backgroundColor: "var(--ev-surface-2)" }}>
            <h3 style={{ fontSize: "14px", fontWeight: 700, color: "var(--ev-dark-purple)", margin: "0 0 4px 0" }}>
              Candidate Story Provenance Pass Audit Log
            </h3>
            <p style={{ fontSize: "12px", color: "var(--ev-muted)", margin: 0 }}>
              Audit trail showing which candidates were accepted vs dropped (e.g. cross-source dupes, old republications, or max caps).
            </p>
          </div>

          <table style={{ width: "100%", fontSize: "12.5px", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ backgroundColor: "var(--ev-surface-3)", borderBottom: "1px solid var(--ev-outline)", textAlign: "left" }}>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)" }}>Candidate Headline</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 120 }}>Triage Rating</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 140 }}>Provenance Verdict</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)", width: 160 }}>Selection Fate</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, color: "var(--ev-dark-purple)" }}>Gate Reason</th>
              </tr>
            </thead>
            <tbody>
              {auditLog.map((row, idx) => (
                <tr
                  key={row.id}
                  style={{
                    borderBottom: idx < auditLog.length - 1 ? "1px solid var(--ev-outline-soft)" : "none",
                    backgroundColor: idx % 2 === 0 ? "#ffffff" : "var(--ev-surface-2)",
                  }}
                >
                  <td style={{ padding: "12px 14px", fontWeight: 600, color: "var(--ev-dark-purple)" }}>{row.title}</td>
                  <td style={{ padding: "12px 14px", color: "var(--ev-muted)" }}>{row.triage_priority} / {row.triage_disposition}</td>
                  <td style={{ padding: "12px 14px" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: 10,
                        fontSize: "11px",
                        fontWeight: 700,
                        backgroundColor: row.provenance_verdict.includes("New") ? "#ecfdf5" : "#fef3c7",
                        color: row.provenance_verdict.includes("New") ? "#065f46" : "#92400e",
                      }}
                    >
                      {row.provenance_verdict}
                    </span>
                  </td>
                  <td style={{ padding: "12px 14px" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: 4,
                        fontSize: "11px",
                        fontWeight: 700,
                        backgroundColor: row.final_fate === "INCLUDED_IN_MAIL" ? "#ecfdf5" : "#f3f4f6",
                        color: row.final_fate === "INCLUDED_IN_MAIL" ? "#065f46" : "var(--ev-muted)",
                        border: `1px solid ${row.final_fate === "INCLUDED_IN_MAIL" ? "#a7f3d0" : "#d1d5db"}`,
                      }}
                    >
                      {row.final_fate}
                    </span>
                  </td>
                  <td style={{ padding: "12px 14px", color: "var(--ev-gray)", fontSize: "12px" }}>{row.gate_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
