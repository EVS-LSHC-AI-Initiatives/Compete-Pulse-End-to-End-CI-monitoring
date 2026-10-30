/**
 * Shared design tokens for MASH CI Monitoring Agent.
 * Follows Evalueserve Brand Guidelines (Media V2).
 */

export const C = {
  // Primary brand palette
  purple: "#43224d",           // Evalueserve Purple - primary colour
  darkPurple: "#301038",       // Evalueserve Dark Purple - hover, depth, headings, sidebar
  red: "#e0044e",              // Evalueserve Red - action highlights, high priority, alert badges
  gray: "#4c4c4d",             // Evalueserve Gray - body text
  white: "#ffffff",

  // Role names
  sidebar: "#301038",          // dark purple
  sidebarAccent: "#43224d",    // sidebar row hover/active surface
  sidebarBorder: "#4a2c55",    // sidebar internal dividers
  accent: "#43224d",           // purple
  cta: "#e0044e",              // red primary CTA
  border: "#dcd8e0",           // border
  text: "#4c4c4d",             // body text
  offWhite: "#f4f3f6",         // page backgrounds
  ink: "#301038",              // headings
  muted: "#6c6c6e",            // secondary text
  subtle: "#767678",           // placeholders, meta, disabled

  // Tints and shades of primary palette
  purple50: "#f5f2f6",         // hover rows, selected backgrounds
  purple100: "#ece9ee",        // chips, active tab backgrounds
  purple200: "#d9d3db",        // tinted borders
  redShade: "#b3033e",         // error text on redTint, red hover
  redTint: "#fde8ef",          // error backgrounds, high priority badge
  redBorder: "#f7c0d3",
  divider: "#e6e6e6",          // soft dividers
  surfaceAlt: "#f8f8f8",       // nested panels inside white cards

  // Surface & outline scale
  canvas: "#f4f3f6",           // page background behind cards
  surface2: "#f9f8fa",         // nested panels, zebra rows
  surface3: "#f0eef3",         // section header bands, table headers
  outline: "#dcd8e0",          // card and section borders
  outlineSoft: "#ebe8ee",      // dividers inside a card
  outlineStrong: "#8f8996",    // input borders

  // Accent & status colors
  blue50: "#f0f4fa",
  blue100: "#dfe8f4",
  blue200: "#bccfe7",
  blueDeep: "#254d84",         // blue text on blue tints

  green50: "#eaf5ea",
  green100: "#d5ecd5",
  green200: "#a9dfa9",
  greenDeep: "#1b6f2b",        // green badge text, success
  diffGreen: "#C6EFCE",        // newly added trial diff background

  // Status & diff colors (Clean EVS palette, zero yellow)
  yellow50: "#f5f2f6",         // mapped to purple50
  yellow100: "#ece9ee",        // mapped to purple100
  yellow200: "#d9d3db",        // mapped to purple200
  yellowDeep: "#43224d",       // mapped to purple
  diffYellow: "#f5f2f6",       // soft purple tint for changed trial diff background

  // Circulation tag color (from Morning Update spec)
  limeCirculation: "#d9f99d",
  limeText: "#365314",
};

export const RADIUS = 8;
export const RADIUS_SM = 4;
export const RADIUS_LG = 12;

export const FONT = {
  sans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  mono: "'DM Mono', 'Consolas', monospace",
};

export const SHADOWS = {
  card: "0 1px 3px rgba(48, 16, 56, 0.05), 0 1px 2px rgba(48, 16, 56, 0.03)",
  elevated: "0 4px 12px rgba(48, 16, 56, 0.08), 0 2px 4px rgba(48, 16, 56, 0.04)",
  modal: "0 12px 28px rgba(48, 16, 56, 0.15), 0 4px 8px rgba(48, 16, 56, 0.08)",
};
