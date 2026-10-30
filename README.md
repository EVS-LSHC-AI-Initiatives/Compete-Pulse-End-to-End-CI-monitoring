# CompetePulse — MASH CI Monitoring Agent Platform

> **Production-grade competitive intelligence platform for MASH / MASLD therapeutic monitoring.**  
> Powered by an automated 4-stage pipeline: OPML Ingestion, URL Deduplication, Parallel AI Triage, and Executive Client Briefing.

---

## 🌐 Live Interactive HTML Workflow for Colleagues

Colleagues can navigate the complete prefilled interactive intelligence workflow directly in their browser without installing Python or Node:

👉 **[Launch CompetePulse Interactive Workflow](https://evs-lshc-ai-initiatives.github.io/Compete-Pulse-End-to-End-CI-monitoring/#/competepulse/dashboard)**

### What You Can Explore in the Web UI:
1. **Executive Overview Dashboard**: High-level KPIs across 592 feeds, 148 extractions, 18 high-priority signals, and active clinical trial diffs.
2. **News Triage & AI Screening**: Multi-faceted table and Kanban views with slide-out article inspection drawer and instant disposition controls.
3. **Client Update Briefing Studio**: Pre-rendered executive email with primary source citations and indented **Evalueserve Suggestion** advisory callouts.
4. **ClinicalTrials.gov Radar**: 310 monitored query programs, 119-field deep study profiles, and color-coded change detection diffs.
5. **Feed Health Diagnostic**: Health status, polling intervals, and latency metrics across all 592 feeds in the 5 OPML sets.
6. **Pipeline Orchestrator**: Step-by-step execution tracker with terminal output logs.

---

## 🧬 Therapeutic Area & Indication Scope

- **Therapeutic Area**: **Hepatology & Cardiometabolic Diseases**
- **Primary Indication**: **MASH / MASLD** (*Metabolic Dysfunction-Associated Steatohepatitis / Steatotic Liver Disease*)
- **Extended In-Scope Indications**: **MetALD & ALD** (*Alcohol-Related Liver Disease*)
- **Out-of-Scope Filter**: Standalone obesity/T2D weight-loss updates without hepatic involvement are automatically vetoed upstream.

---

## 🚀 Quick Start (Local Development)

### Option 1: One-Click Startup (Recommended)
Double-click `start-app.bat` to automatically launch the FastAPI backend (port `8792`), the Vite frontend (port `5174`), and open the application in your browser:
```cmd
start-app.bat
```

### Option 2: Cloudflare Public Sharing Tunnel
Double-click `start-tunnel.bat` to launch both servers and create an instant, shareable Cloudflare tunnel URL:
```cmd
start-tunnel.bat
```

### Option 3: Manual Startup
```cmd
# Terminal 1: Backend (FastAPI on port 8792)
cd backend
python -m uvicorn app.main:app --port 8792 --reload

# Terminal 2: Frontend (Vite on port 5174)
cd frontend
npm install
npm run dev
```

---

## 🏗️ Architecture & Tech Stack

- **Frontend**: React 18, Vite 5, React Router 6, Recharts, Lucide Icons, Evalueserve Brand System.
- **Backend**: FastAPI, Uvicorn, Pydantic, Python 3.11+.
- **Data Persistence**: Zero-database structured JSON store (`backend/data/`). All UI actions persist atomically to disk.
- **Cloud Deployment**: Google Cloud Build (`cloudbuild.yaml`), Google Cloud Run, Docker Compose (`docker-compose.yml`), and GitHub Actions (`.github/workflows/deploy.yml`).
