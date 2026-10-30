import React from "react";

// Official Evalueserve vector artwork from reference project
const LOGO_B64 = "data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 740 120' preserveAspectRatio='xMidYMid meet'%3E%3Cg fill='%23FFFFFF'%3E%3Cpolygon points='0 118.01 47.37 118.01 47.37 106.19 12.85 106.19 12.85 88.78 44.15 88.78 44.15 77.39 12.85 77.39 12.85 56.96 47.37 56.96 47.37 45.14 0 45.14 0 118.01'/%3E%3Cpolygon points='116.19 45.15 93.8 94.9 71.52 45.15 57.73 45.15 91.52 118.94 95.87 118.94 129.66 45.15 116.19 45.15'/%3E%3Cpath d='M146.76 93.24l11.09-25.08 11.09 25.08h-22.18zM156.61 44.52l-34.21 73.49h13.48l6.32-14.51h31.3l6.43 14.51h13.79l-34.21-73.49h-2.9z'/%3E%3Cpolygon points='218.78 45.14 205.93 45.14 205.93 118.01 253.28 118.01 253.28 106.2 218.78 106.2 218.78 45.14'/%3E%3Cpath d='M311.68 87.75c0 13.98-6.74 19.79-17.73 19.79s-17.3-5.81-17.3-19.7v-42.7h-12.86v42.7c0 21.77 11.92 31.51 30.16 31.51s30.58-9.74 30.58-31.51v-42.7h-12.85v42.6z'/%3E%3Cpolygon points='344.23 117.86 391.6 117.86 391.6 106.04 357.09 106.04 357.09 88.63 388.39 88.63 388.39 77.23 357.09 77.23 357.09 56.81 391.6 56.81 391.6 44.99 344.23 44.99 344.23 117.86'/%3E%3Cpath d='M436.9 75.88c-10.26-3.73-14.2-6.64-14.2-11.3.0-5.07 3.52-9.33 11.19-9.33 5.6.0 12.02 2.48 16.28 4.87l4.15-11.4c-4.66-2.7-12.33-4.98-20.1-4.98-15.55.0-24.47 10.16-24.47 21.14.0 8.71 4.86 15.44 19.07 20.62 13.16 4.87 15.85 7.78 15.85 12.65s-3.93 8.91-12.53 8.91c-6.53.0-13.79-2.49-19.8-6.53l-4.45 11.61c6.62 4.03 15.23 6.94 23.83 6.94 16.27.0 26.12-7.98 26.12-20.93.0-9.54-4.67-15.97-20.93-22.28'/%3E%3Cpolygon points='475.83 117.86 523.2 117.86 523.2 106.04 488.69 106.04 488.69 88.63 519.99 88.63 519.99 77.23 488.69 77.23 488.69 56.81 523.2 56.81 523.2 44.99 475.83 44.99 475.83 117.86'/%3E%3Cpath d='M565.18 85.31h-9.54V56.8h9.54c12.12.0 16.16 6.11 16.16 14.1s-4.14 14.41-16.16 14.41M581.66 93.91c9.74-4.78 13.58-15.45 13.58-23.01.0-15.23-9.74-25.91-29.12-25.91H542.8v72.86h12.85V96.7h7.98c1.35.0 2.59.0 3.83-.09 8.81 4.97 7.78 21.65 25.91 21.65 4.25.0 6.95-.62 6.95-.62v-11.4h-2.91c-8.7.0-10.26-7.56-15.75-12.33'/%3E%3Cpolygon points='640.43 94.74 618.15 44.99 604.36 44.99 638.15 118.78 642.5 118.78 676.29 44.99 662.82 44.99 640.43 94.74'/%3E%3Cpolygon points='736.93 56.81 736.93 44.99 689.56 44.99 689.56 117.86 736.93 117.86 736.93 106.04 702.41 106.04 702.41 88.63 733.71 88.63 733.71 77.23 702.41 77.23 702.41 56.81 736.93 56.81'/%3E%3C/g%3E%3C/svg%3E";

const EVA_LOGO_INK_CENTER_OFFSET_FRAC = 0.163;

export default function BrandLockup({
  size = 14,
  dark = false,
  showProduct = true,
  collapsed = false,
}) {
  if (collapsed) {
    return (
      <div
        title="CompetePulse — Evalueserve CI Platform"
        style={{
          width: 34,
          height: 34,
          borderRadius: 8,
          background: dark ? "rgba(67, 34, 77, 0.12)" : "rgba(255, 255, 255, 0.12)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto",
        }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path
            d="M3 12h4l3-8 4 16 3-8h4"
            stroke={dark ? "var(--ev-purple)" : "#ffffff"}
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5, width: "100%", overflow: "hidden" }}>
      {/* Top Line: Authentic Evalueserve Logo */}
      <div style={{ display: "flex", alignItems: "center" }}>
        <img
          src={LOGO_B64}
          alt="Evalueserve"
          style={{
            height: size,
            width: "auto",
            display: "block",
            transform: `translateY(-${(size * EVA_LOGO_INK_CENTER_OFFSET_FRAC).toFixed(1)}px)`,
            filter: dark ? "invert(1)" : "none",
          }}
        />
      </div>

      {/* Second Line: CompetePulse Product Title */}
      {showProduct && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 1 }}>
          <span
            style={{
              fontFamily: "'Inter', sans-serif",
              fontSize: 14.5,
              fontWeight: 800,
              letterSpacing: "-0.02em",
              color: dark ? "var(--ev-purple)" : "#ffffff",
              lineHeight: 1.1,
              whiteSpace: "nowrap",
            }}
          >
            CompetePulse
          </span>
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              padding: "2px 5px",
              borderRadius: 4,
              background: "var(--ev-red)",
              color: "#ffffff",
              lineHeight: 1,
            }}
          >
            CI
          </span>
        </div>
      )}

      {/* Third Line: Therapeutic Area & Indication Badge */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 5,
          fontSize: 10,
          color: dark ? "var(--ev-muted)" : "rgba(255, 255, 255, 0.7)",
          fontWeight: 600,
          marginTop: 2,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "#107c41",
            boxShadow: "0 0 6px rgba(16, 124, 65, 0.5)",
            flexShrink: 0,
          }}
        />
        <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          TA: Hepatology · MASH
        </span>
      </div>
    </div>
  );
}
