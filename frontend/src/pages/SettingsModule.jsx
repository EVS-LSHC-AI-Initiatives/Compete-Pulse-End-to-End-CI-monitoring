import React, { useState, useEffect } from "react";
import {
  Settings,
  Save,
  Check,
  Plus,
  X,
  Mail,
  User,
  ShieldAlert,
  Sliders,
  Clock,
  Layers
} from "lucide-react";
import { C, SHADOWS, RADIUS } from "../theme.js";
import { api } from "../api.js";
import { PageLoader } from "../components/loaders/index.jsx";

export default function SettingsModule() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saved, setSaved] = useState(false);
  const [newDenyDomain, setNewDenyDomain] = useState("");

  useEffect(() => {
    async function loadSettings() {
      try {
        const s = await api.getSettings();
        setSettings(s);
      } catch (err) {
        console.error("Failed to load settings", err);
      } finally {
        setLoading(false);
      }
    }
    loadSettings();
  }, []);

  const handleSave = async () => {
    try {
      await api.saveSettings(settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      console.error("Failed to save settings", err);
    }
  };

  const handleAddDenyDomain = () => {
    if (!newDenyDomain.trim()) return;
    const clean = newDenyDomain.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (!settings.source_denylist.includes(clean)) {
      setSettings({
        ...settings,
        source_denylist: [...settings.source_denylist, clean],
      });
      setNewDenyDomain("");
    }
  };

  const handleRemoveDenyDomain = (domain) => {
    setSettings({
      ...settings,
      source_denylist: settings.source_denylist.filter((d) => d !== domain),
    });
  };

  if (loading || !settings) {
    return <PageLoader text="Loading Agent Settings..." />;
  }

  return (
    <div style={{ padding: "28px 36px", maxWidth: 1100, margin: "0 auto", overflowY: "auto" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: "22px", fontWeight: 800, color: C.ink, margin: "0 0 6px 0" }}>
            Agent Settings & Governance
          </h1>
          <p style={{ fontSize: "13px", color: C.muted, margin: 0 }}>
            Configure client recipients, lookback windows, source denylists, and selection rules (mail_settings.json).
          </p>
        </div>

        <button
          onClick={handleSave}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "9px 20px",
            borderRadius: 6,
            backgroundColor: saved ? C.greenDeep : C.purple,
            color: "#ffffff",
            border: "none",
            fontSize: "13px",
            fontWeight: 700,
            cursor: "pointer",
            boxShadow: SHADOWS.card,
            transition: "background-color 0.2s ease",
          }}
        >
          {saved ? <Check size={16} /> : <Save size={16} />}
          {saved ? "Settings Saved!" : "Save Changes"}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {/* Section 1: Client Email & Consultant */}
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            border: `1px solid ${C.outline}`,
            padding: "24px",
            boxShadow: SHADOWS.card,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
            <Mail size={18} color={C.purple} />
            <h2 style={{ fontSize: "15px", fontWeight: 700, color: C.ink, margin: 0 }}>
              Client Recipients & Consultant Signature
            </h2>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Primary Recipient Name:
              </label>
              <input
                type="text"
                value={settings.greeting_name || ""}
                onChange={(e) => setSettings({ ...settings, greeting_name: e.target.value })}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: `1px solid ${C.outlineStrong}`,
                  fontSize: "13px",
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Consultant Name:
              </label>
              <input
                type="text"
                value={settings.consultant?.name || ""}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    consultant: { ...settings.consultant, name: e.target.value },
                  })
                }
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: `1px solid ${C.outlineStrong}`,
                  fontSize: "13px",
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Consultant Title & Practice:
              </label>
              <input
                type="text"
                value={settings.consultant?.title || ""}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    consultant: { ...settings.consultant, title: e.target.value },
                  })
                }
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: `1px solid ${C.outlineStrong}`,
                  fontSize: "13px",
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Consultant Contact Phone:
              </label>
              <input
                type="text"
                value={settings.consultant?.phone || ""}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    consultant: { ...settings.consultant, phone: e.target.value },
                  })
                }
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: `1px solid ${C.outlineStrong}`,
                  fontSize: "13px",
                }}
              />
            </div>
          </div>
        </div>

        {/* Section 2: Lookback & Caps */}
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            border: `1px solid ${C.outline}`,
            padding: "24px",
            boxShadow: SHADOWS.card,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
            <Clock size={18} color={C.purple} />
            <h2 style={{ fontSize: "15px", fontWeight: 700, color: C.ink, margin: 0 }}>
              Lookback Windows & Item Volume Ceilings
            </h2>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16 }}>
            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Morning Lookback (Hours):
              </label>
              <input
                type="number"
                value={settings.lookback_hours_by_slot?.Morning || 20}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    lookback_hours_by_slot: {
                      ...settings.lookback_hours_by_slot,
                      Morning: parseInt(e.target.value, 10),
                    },
                  })
                }
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: `1px solid ${C.outlineStrong}`, fontSize: "13px" }}
              />
            </div>

            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Evening Lookback (Hours):
              </label>
              <input
                type="number"
                value={settings.lookback_hours_by_slot?.Evening || 12}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    lookback_hours_by_slot: {
                      ...settings.lookback_hours_by_slot,
                      Evening: parseInt(e.target.value, 10),
                    },
                  })
                }
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: `1px solid ${C.outlineStrong}`, fontSize: "13px" }}
              />
            </div>

            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Max Items Per Mail:
              </label>
              <input
                type="number"
                value={settings.max_items_total || 5}
                onChange={(e) => setSettings({ ...settings, max_items_total: parseInt(e.target.value, 10) })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: `1px solid ${C.outlineStrong}`, fontSize: "13px" }}
              />
            </div>

            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: C.subtle, display: "block", marginBottom: 4 }}>
                Max Items Per Section:
              </label>
              <input
                type="number"
                value={settings.max_items_per_section || 4}
                onChange={(e) => setSettings({ ...settings, max_items_per_section: parseInt(e.target.value, 10) })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: `1px solid ${C.outlineStrong}`, fontSize: "13px" }}
              />
            </div>
          </div>
        </div>

        {/* Section 3: Source Denylist */}
        <div
          style={{
            backgroundColor: "#ffffff",
            borderRadius: 8,
            border: `1px solid ${C.outline}`,
            padding: "24px",
            boxShadow: SHADOWS.card,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <ShieldAlert size={18} color={C.red} />
            <h2 style={{ fontSize: "15px", fontWeight: 700, color: C.ink, margin: 0 }}>
              Source Host Denylist ({settings.source_denylist?.length || 0} Domains)
            </h2>
          </div>
          <p style={{ fontSize: "12px", color: C.muted, margin: "0 0 16px 0" }}>
            Hosts that never carry citable primary intelligence (social networks, video platforms, retail investor listicles). Dropped pre-triage to save AI spend.
          </p>

          {/* Add input */}
          <div style={{ display: "flex", gap: 8, marginBottom: 16, maxWidth: 450 }}>
            <input
              type="text"
              placeholder="e.g. reddit.com, twitter.com"
              value={newDenyDomain}
              onChange={(e) => setNewDenyDomain(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddDenyDomain()}
              style={{
                flex: 1,
                padding: "7px 12px",
                borderRadius: 6,
                border: `1px solid ${C.outlineStrong}`,
                fontSize: "12.5px",
              }}
            />
            <button
              onClick={handleAddDenyDomain}
              style={{
                padding: "7px 14px",
                borderRadius: 6,
                border: "none",
                backgroundColor: C.purple,
                color: "#ffffff",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <Plus size={14} /> Add Domain
            </button>
          </div>

          {/* Chips */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {settings.source_denylist?.map((domain) => (
              <span
                key={domain}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: 14,
                  backgroundColor: C.surface3,
                  border: `1px solid ${C.outline}`,
                  fontSize: "12px",
                  color: C.ink,
                }}
              >
                {domain}
                <button
                  onClick={() => handleRemoveDenyDomain(domain)}
                  style={{
                    border: "none",
                    background: "none",
                    cursor: "pointer",
                    padding: 0,
                    color: C.subtle,
                    display: "flex",
                  }}
                >
                  <X size={13} />
                </button>
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
