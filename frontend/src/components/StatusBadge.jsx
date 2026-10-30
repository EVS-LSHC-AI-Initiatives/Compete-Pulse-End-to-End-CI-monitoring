import React from "react";
import {
  AlertTriangle,
  Clock,
  ArrowDownRight,
  CheckCircle2,
  XCircle,
  Check,
  Bookmark,
  X,
  CheckCircle,
  AlertCircle,
  FlaskConical,
  Sparkles
} from "lucide-react";
import { C } from "../theme.js";

export function PriorityBadge({ priority }) {
  const p = (priority || "").toLowerCase();
  let bg = "#f4f4f5";
  let color = "#52525b";
  let border = "#e4e4e7";
  let Icon = Clock;

  if (p === "high") {
    bg = "var(--ev-red-tint)";
    color = "var(--ev-red-shade)";
    border = "var(--ev-red-border)";
    Icon = AlertTriangle;
  } else if (p === "medium") {
    bg = "var(--ev-purple-50)";
    color = "var(--ev-purple)";
    border = "var(--ev-purple-200)";
    Icon = Clock;
  } else if (p === "low") {
    bg = "var(--ev-blue-50)";
    color = "var(--ev-blue-deep)";
    border = "var(--ev-blue-200)";
    Icon = ArrowDownRight;
  }

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: "12px",
        fontSize: "11px",
        fontWeight: 700,
        backgroundColor: bg,
        color: color,
        border: `1px solid ${border}`,
        letterSpacing: "0.2px",
        textTransform: "uppercase",
      }}
    >
      <Icon size={11} />
      <span>{priority || "Unassigned"}</span>
    </span>
  );
}

export function DispositionBadge({ disposition }) {
  const d = (disposition || "").toLowerCase();
  let bg = "#f4f4f5";
  let color = "#3f3f46";
  let border = "#d4d4d8";
  let Icon = Clock;

  if (d.includes("approved") || d.includes("draft") || d.includes("send")) {
    bg = "var(--ev-purple-50)";
    color = "var(--ev-purple)";
    border = "var(--ev-purple-200)";
    Icon = Check;
  } else if (d.includes("watchlist") || d.includes("tracker")) {
    bg = "var(--ev-blue-50)";
    color = "var(--ev-blue-deep)";
    border = "var(--ev-blue-200)";
    Icon = Bookmark;
  } else if (d.includes("rejected") || d.includes("no action") || d.includes("dropped")) {
    bg = "#f3f4f6";
    color = "#6b7280";
    border = "#e5e7eb";
    Icon = X;
  }

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: "6px",
        fontSize: "11px",
        fontWeight: 600,
        backgroundColor: bg,
        color: color,
        border: `1px solid ${border}`,
      }}
    >
      <Icon size={11} />
      <span>{disposition || "Pending Review"}</span>
    </span>
  );
}

export function ScopeBadge({ scope }) {
  const isMASH = (scope || "").toLowerCase().includes("in scope") || (scope || "").toLowerCase().includes("mash");

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 7px",
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: 600,
        backgroundColor: isMASH ? "#ecfdf5" : "#fef2f2",
        color: isMASH ? "#065f46" : "#991b1b",
        border: `1px solid ${isMASH ? "#a7f3d0" : "#fecaca"}`,
      }}
    >
      {isMASH ? <CheckCircle2 size={11} color="#065f46" /> : <XCircle size={11} color="#991b1b" />}
      <span>{scope || "Out of Scope"}</span>
    </span>
  );
}

export function FeedStatusBadge({ status }) {
  const s = (status || "").toLowerCase();
  let bg = "#ecfdf5";
  let color = "#065f46";
  let border = "#a7f3d0";
  let label = "Healthy";
  let Icon = CheckCircle;

  if (s === "warning" || s === "warn") {
    bg = "var(--ev-purple-50)";
    color = "var(--ev-purple)";
    border = "var(--ev-purple-200)";
    label = "Warning";
    Icon = AlertTriangle;
  } else if (s === "error" || s === "failing") {
    bg = "#fef2f2";
    color = "#991b1b";
    border = "#fecaca";
    label = "Error";
    Icon = AlertCircle;
  }

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: "12px",
        fontSize: "11px",
        fontWeight: 700,
        backgroundColor: bg,
        color: color,
        border: `1px solid ${border}`,
      }}
    >
      <Icon size={11} />
      <span>{label}</span>
    </span>
  );
}

export function PhaseBadge({ phase }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 7px",
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: 600,
        backgroundColor: "var(--ev-purple-50)",
        color: "var(--ev-purple)",
        border: "1px solid var(--ev-purple-200)",
      }}
    >
      <FlaskConical size={11} />
      <span>{phase || "N/A"}</span>
    </span>
  );
}
