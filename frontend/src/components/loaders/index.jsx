import React from "react";
import { C } from "../../theme.js";

export function Spinner({ size = 20, color = C.purple }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        border: `2px solid ${color}22`,
        borderTop: `2px solid ${color}`,
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
        display: "inline-block",
      }}
    />
  );
}

export function Skeleton({ width = "100%", height = 20, radius = 4, style = {} }) {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: radius,
        backgroundColor: "#e8e5eb",
        animation: "pulse 1.5s ease-in-out infinite",
        ...style,
      }}
    />
  );
}

export function PageLoader({ text = "Loading CI Intelligence..." }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        minHeight: 300,
        gap: 16,
        color: C.muted,
      }}
    >
      <Spinner size={32} color={C.purple} />
      <span style={{ fontSize: "14px", fontWeight: 500 }}>{text}</span>
    </div>
  );
}
