import React from "react";
import { Rss, GitMerge, Sparkles, Mail, CheckCircle2, ArrowRight } from "lucide-react";
import { C } from "../theme.js";

const STAGES = [
  { id: 1, name: "RSS Ingestion", desc: "5 OPML pipelines (592 feeds)", icon: Rss },
  { id: 2, name: "Link Deduplication", desc: "Google redirect & canonical resolver", icon: GitMerge },
  { id: 3, name: "Parallel AI Triage", desc: "Priority, disposition & takeaways", icon: Sparkles },
  { id: 4, name: "Executive Brief", desc: "Morning/Evening draft with consultant advice", icon: Mail },
];

export default function PipelineStepper({ activeStage = 3, onStageClick }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
        gap: 12,
        marginBottom: 20,
      }}
    >
      {STAGES.map((s, idx) => {
        const Icon = s.icon;
        const isActive = activeStage === s.id;
        const isPast = activeStage > s.id;

        return (
          <div
            key={s.id}
            onClick={() => onStageClick && onStageClick(s.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 14px",
              borderRadius: 8,
              backgroundColor: isActive ? C.purple50 : isPast ? "#ffffff" : "#ffffff",
              border: isActive
                ? `1.5px solid ${C.purple}`
                : isPast
                ? `1px solid ${C.green200}`
                : `1px solid ${C.outline}`,
              cursor: onStageClick ? "pointer" : "default",
              transition: "all 0.15s ease",
              boxShadow: isActive ? "0 2px 8px rgba(67, 34, 77, 0.08)" : "none",
            }}
          >
            <div
              style={{
                width: 34,
                height: 34,
                borderRadius: 8,
                backgroundColor: isActive ? C.purple : isPast ? C.green50 : C.surface3,
                color: isActive ? "#ffffff" : isPast ? C.greenDeep : C.muted,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              {isPast ? <CheckCircle2 size={18} /> : <Icon size={17} />}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div
                style={{
                  fontSize: "10.5px",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: isActive ? C.purple : isPast ? C.greenDeep : C.subtle,
                }}
              >
                Stage {s.id}
              </div>
              <div
                style={{
                  fontSize: "13px",
                  fontWeight: 600,
                  color: C.ink,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {s.name}
              </div>
              <div
                style={{
                  fontSize: "11px",
                  color: C.muted,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {s.desc}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
