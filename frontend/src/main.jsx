import React, { useState, useEffect } from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Routes, Route, Navigate, useNavigate, useLocation } from "react-router-dom";
import AppSidebar, { MODULES, MODULE_TOOLS } from "./components/AppSidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import DashboardOverview from "./pages/DashboardOverview.jsx";
import NewsTriageModule from "./pages/NewsTriageModule.jsx";
import MorningUpdateModule from "./pages/MorningUpdateModule.jsx";
import ClinicalTrialsModule from "./pages/ClinicalTrialsModule.jsx";
import FeedHealthModule from "./pages/FeedHealthModule.jsx";
import PipelineRunnerModule from "./pages/PipelineRunnerModule.jsx";
import SettingsModule from "./pages/SettingsModule.jsx";
import { api } from "./api.js";
import "./theme.css";

function CompetePulseShell() {
  const navigate = useNavigate();
  const location = useLocation();

  // Determine current page from URL path: /competepulse/:page
  const pathParts = location.pathname.split("/").filter(Boolean);
  const currentPathPage = pathParts[1] || "dashboard";

  // Derive active module from current page
  const getModuleForPage = (pageId) => {
    for (const [modKey, tools] of Object.entries(MODULE_TOOLS)) {
      if (tools.some((t) => t.id === pageId)) {
        return modKey;
      }
    }
    return "news";
  };

  const [activeModule, setActiveModule] = useState(() => getModuleForPage(currentPathPage));
  const [activePage, setActivePage] = useState(currentPathPage);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [apiConnected, setApiConnected] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Sync state when URL changes
  useEffect(() => {
    setActivePage(currentPathPage);
    setActiveModule(getModuleForPage(currentPathPage));
  }, [currentPathPage]);

  // Periodic healthcheck against FastAPI backend
  useEffect(() => {
    async function checkBackend() {
      const ok = await api.checkHealth();
      setApiConnected(ok);
    }
    checkBackend();
    const timer = setInterval(checkBackend, 15000);
    return () => clearInterval(timer);
  }, []);

  const handlePageSelect = (pageId) => {
    setActivePage(pageId);
    navigate(`/competepulse/${pageId}`);
  };

  const handleModuleSelect = (modKey) => {
    setActiveModule(modKey);
    const tools = MODULE_TOOLS[modKey];
    if (tools && tools.length > 0) {
      handlePageSelect(tools[0].id);
    }
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    const ok = await api.checkHealth();
    setApiConnected(ok);
    setTimeout(() => {
      setIsRefreshing(false);
    }, 800);
  };

  const handleQuickRun = () => {
    handlePageSelect("runner");
  };

  const renderActivePage = () => {
    switch (activePage) {
      case "dashboard":
        return <DashboardOverview onNavigate={handlePageSelect} />;
      case "triage":
        return <NewsTriageModule />;
      case "morning-update":
        return <MorningUpdateModule />;
      case "clinical-trials":
        return <ClinicalTrialsModule />;
      case "feeds":
        return <FeedHealthModule />;
      case "runner":
        return <PipelineRunnerModule />;
      case "settings":
        return <SettingsModule />;
      default:
        return <DashboardOverview onNavigate={handlePageSelect} />;
    }
  };

  return (
    <div style={{ display: "flex", width: "100vw", height: "100vh", overflow: "hidden", backgroundColor: "var(--ev-canvas)" }}>
      {sidebarOpen && (
        <AppSidebar
          activeModule={activeModule}
          setActiveModule={handleModuleSelect}
          activePage={activePage}
          setActivePage={handlePageSelect}
          sidebarOpen={sidebarOpen}
          setSidebarOpen={setSidebarOpen}
          apiConnected={apiConnected}
        />
      )}

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, height: "100vh", overflow: "hidden" }}>
        <TopBar
          activeModule={activeModule}
          activePage={activePage}
          sidebarOpen={sidebarOpen}
          setSidebarOpen={setSidebarOpen}
          onQuickRun={handleQuickRun}
          onRefresh={handleRefresh}
          isRefreshing={isRefreshing}
        />

        <main
          style={{
            flex: 1,
            overflowY: "auto",
            backgroundColor: "var(--ev-canvas)",
          }}
        >
          {renderActivePage()}
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/competepulse/dashboard" replace />} />
        <Route path="/competepulse" element={<Navigate to="/competepulse/dashboard" replace />} />
        <Route path="/competepulse/:page" element={<CompetePulseShell />} />
        <Route path="*" element={<Navigate to="/competepulse/dashboard" replace />} />
      </Routes>
    </HashRouter>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
