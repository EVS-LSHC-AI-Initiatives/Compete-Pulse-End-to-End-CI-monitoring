/**
 * Central API Gateway for CompetePulse MASH CI Monitoring Agent.
 * Interacts directly with FastAPI backend (/api/...) and falls back to local dataService
 * if backend is temporarily disconnected.
 */

import { dataService } from "./services/dataService.js";

const BASE = "/api";

async function request(path, options = {}) {
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      throw new Error(`API error ${res.status}: ${res.statusText}`);
    }
    return await res.json();
  } catch (err) {
    // Return null on failure to allow caller fallback
    return null;
  }
}

export const api = {
  // Health & connection status
  checkHealth: async () => {
    try {
      const res = await fetch("/healthz");
      if (res.ok) {
        const body = await res.json();
        return body.status === "ok";
      }
      return false;
    } catch {
      return false;
    }
  },

  // Metrics & overview
  getMetrics: async () => {
    const backendData = await request("/overview");
    if (backendData && backendData.kpis) {
      return {
        rawExtractions: backendData.kpis.total_raw_extractions,
        deduped: backendData.kpis.deduped_items,
        triaged: backendData.kpis.triaged_items,
        highPriority: backendData.kpis.high_priority_items,
        inScope: backendData.kpis.in_scope_items,
        approvedBrief: backendData.kpis.approved_items,
        pendingReview: backendData.kpis.pending_review_items,
        totalFeeds: backendData.kpis.total_feeds,
        healthyFeeds: backendData.kpis.healthy_feeds,
        warningFeeds: backendData.kpis.warning_feeds,
        errorFeeds: backendData.kpis.error_feeds,
        activeTrials: backendData.kpis.monitored_clinical_queries,
        trialDiffs: backendData.kpis.active_change_alerts,
      };
    }
    return dataService.getDashboardMetrics();
  },

  // News & AI Triage
  getTriagedItems: async (filters = {}) => {
    const params = new URLSearchParams();
    if (filters.search) params.append("search", filters.search);
    if (filters.priority) params.append("priority", filters.priority);
    if (filters.scope) params.append("scope", filters.scope);
    if (filters.disposition) params.append("disposition", filters.disposition);
    if (filters.category) params.append("category", filters.category);
    if (filters.page) params.append("page", filters.page);
    if (filters.pageSize) params.append("page_size", filters.pageSize);

    const query = params.toString() ? `?${params.toString()}` : "";
    const backendData = await request(`/news${query}`);
    if (backendData && Array.isArray(backendData.items)) {
      return {
        items: backendData.items,
        total: backendData.total,
        page: backendData.page,
        pageSize: backendData.page_size,
      };
    }
    return dataService.getTriagedItems(filters);
  },

  // Update single article disposition/priority
  updateArticle: async (id, patch) => {
    const backendRes = await request(`/news/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    if (backendRes) {
      return backendRes;
    }
    return dataService.updateDraftItem(id, patch);
  },

  // Morning / Evening draft
  getMorningDraft: async (slot = "Morning") => {
    const backendData = await request("/morning-update");
    if (backendData && backendData.draft) {
      return backendData.draft;
    }
    return dataService.getMorningDraft(slot);
  },

  saveMorningDraft: async (draft) => {
    const backendRes = await request("/morning-update", {
      method: "PUT",
      body: JSON.stringify({ draft }),
    });
    if (backendRes) {
      return backendRes;
    }
    return { success: true };
  },

  getSelectionLog: () => dataService.getSelectionAuditLog(),

  // Clinical trials
  getTrials: async (filters = {}) => {
    const params = new URLSearchParams();
    if (filters.search) params.append("search", filters.search);
    if (filters.phase) params.append("phase", filters.phase);
    if (filters.status) params.append("status", filters.status);

    const query = params.toString() ? `?${params.toString()}` : "";
    const backendData = await request(`/trials${query}`);
    if (backendData && Array.isArray(backendData.trials)) {
      return backendData.trials;
    }
    return dataService.getClinicalTrials(filters);
  },

  getQueries: async (search) => {
    const query = search ? `?search=${encodeURIComponent(search)}` : "";
    const backendData = await request(`/trials/queries${query}`);
    if (backendData && Array.isArray(backendData.queries)) {
      return backendData.queries;
    }
    return dataService.getSearchQueries(search);
  },

  getTrialChanges: async () => {
    const backendData = await request("/trials/diffs");
    if (backendData && Array.isArray(backendData.diffs)) {
      return backendData.diffs;
    }
    return dataService.getTrialChanges();
  },

  // Feed health
  getFeedHealth: async (category, search) => {
    const params = new URLSearchParams();
    if (category) params.append("category", category);
    if (search) params.append("search", search);

    const query = params.toString() ? `?${params.toString()}` : "";
    const backendData = await request(`/feeds${query}`);
    if (backendData && Array.isArray(backendData.feeds)) {
      return {
        summary: backendData.summary,
        categories: backendData.categories,
        feeds: backendData.feeds,
      };
    }
    return dataService.getFeedHealth(category, search);
  },

  // Pipeline execution
  getRuns: async () => {
    const backendData = await request("/pipeline/runs");
    if (backendData && Array.isArray(backendData.runs)) {
      return backendData.runs;
    }
    return dataService.getPipelineRuns();
  },

  triggerRun: async (stage = "all", slot = "Morning") => {
    const backendData = await request("/pipeline/run", {
      method: "POST",
      body: JSON.stringify({ stage, slot }),
    });
    if (backendData && backendData.run) {
      return backendData.run;
    }
    return dataService.triggerPipelineRun(stage, { slot });
  },

  // Settings
  getSettings: async () => {
    const backendData = await request("/settings");
    if (backendData && backendData.settings) {
      return backendData.settings;
    }
    return dataService.getSettings();
  },

  saveSettings: async (settings) => {
    const backendRes = await request("/settings", {
      method: "PUT",
      body: JSON.stringify({ settings }),
    });
    if (backendRes && backendRes.settings) {
      return backendRes.settings;
    }
    return dataService.saveSettings(settings);
  },
};
