/**
 * Data Service for MASH CI Monitoring Agent.
 *
 * Provides a pure zero-database asynchronous data layer that reads
 * directly from structured JSON datasets. Supports in-memory mutations
 * (e.g. updating settings, marking items reviewed, filtering, search).
 */

import newsDataImport from "../data/news_pipeline_data.json";
import ctDataImport from "../data/clinical_trials_data.json";
import feedDataImport from "../data/feed_health_data.json";
import mailSettingsImport from "../data/mail_settings.json";
import pipelineRunsImport from "../data/pipeline_runs.json";

// In-memory state holding JSON copies
let newsData = JSON.parse(JSON.stringify(newsDataImport));
let ctData = JSON.parse(JSON.stringify(ctDataImport));
let feedData = JSON.parse(JSON.stringify(feedDataImport));
let mailSettings = JSON.parse(JSON.stringify(mailSettingsImport));
let pipelineRuns = JSON.parse(JSON.stringify(pipelineRunsImport));

export const dataService = {
  // 1. Dashboard Overview
  async getDashboardMetrics() {
    return {
      totalFeeds: feedData.summary.total_feeds,
      healthyFeeds: feedData.summary.ok_count,
      healthPercent: feedData.summary.health_percentage,
      totalExtracted: newsData.summary.total_extracted,
      dedupedCount: newsData.summary.deduped_survivors,
      triagedInScope: newsData.summary.triaged_in_scope,
      highPriorityCount: newsData.summary.high_priority_count,
      mediumPriorityCount: newsData.summary.medium_priority_count,
      lowPriorityCount: newsData.summary.low_priority_count,
      draftReady: newsData.summary.mail_draft_ready,
      draftItemsCount: newsData.summary.morning_draft_items_count,
      totalPrograms: ctData.summary.total_tracked_programs,
      trialsEnriched: ctData.summary.total_trials_enriched,
      changesDetected: ctData.summary.total_changes_detected,
      newTrialsCount: ctData.summary.new_trials_count,
      lastExtractionTime: feedData.summary.last_run_timestamp,
    };
  },

  // 2. News Ingestion & AI Triage
  async getTriagedItems(filters = {}) {
    let items = [...newsData.triaged_items];

    if (filters.search) {
      const q = filters.search.toLowerCase();
      items = items.filter(
        (it) =>
          it.title.toLowerCase().includes(q) ||
          (it.company && it.company.toLowerCase().includes(q)) ||
          (it.takeaway && it.takeaway.toLowerCase().includes(q))
      );
    }

    if (filters.priority && filters.priority !== "All") {
      items = items.filter((it) => it.priority.toLowerCase() === filters.priority.toLowerCase());
    }

    if (filters.disposition && filters.disposition !== "All") {
      items = items.filter((it) => it.disposition.toLowerCase() === filters.disposition.toLowerCase());
    }

    if (filters.company && filters.company !== "All") {
      items = items.filter((it) => it.company === filters.company);
    }

    if (filters.scope && filters.scope !== "All") {
      items = items.filter((it) => it.scope === filters.scope);
    }

    return items;
  },

  // 3. Morning / Evening Brief Studio
  async getMorningDraft(slot = "Morning") {
    const draft = JSON.parse(JSON.stringify(newsData.morning_draft));
    draft.slot = slot;
    return draft;
  },

  async updateDraftItem(itemId, updates) {
    let found = false;
    for (const sec of newsData.morning_draft.sections) {
      for (let i = 0; i < sec.items.length; i++) {
        if (sec.items[i].id === itemId) {
          sec.items[i] = { ...sec.items[i], ...updates };
          found = true;
          break;
        }
      }
      if (found) break;
    }
    return newsData.morning_draft;
  },

  async getSelectionAuditLog() {
    return newsData.selection_audit_log || [];
  },

  // 4. ClinicalTrials.gov Monitor & Diff Engine
  async getClinicalTrials(filters = {}) {
    let list = [...ctData.trials];
    if (filters.search) {
      const q = filters.search.toLowerCase();
      list = list.filter(
        (t) =>
          t.title.toLowerCase().includes(q) ||
          t.nct_id.toLowerCase().includes(q) ||
          t.program.toLowerCase().includes(q) ||
          t.sponsor.toLowerCase().includes(q)
      );
    }
    if (filters.phase && filters.phase !== "All") {
      list = list.filter((t) => t.phase === filters.phase);
    }
    if (filters.status && filters.status !== "All") {
      list = list.filter((t) => t.status === filters.status);
    }
    return list;
  },

  async getSearchQueries(search = "") {
    let queries = [...ctData.queries];
    if (search) {
      const q = search.toLowerCase();
      queries = queries.filter(
        (row) =>
          row.program.toLowerCase().includes(q) ||
          row.query_string.toLowerCase().includes(q)
      );
    }
    return queries;
  },

  async getTrialChanges() {
    return ctData.changes || [];
  },

  // 5. Feed Health & OPML
  async getFeedHealth(category = "All", search = "") {
    let list = [...feedData.feeds];
    if (category && category !== "All") {
      list = list.filter((f) => f.source_category === category);
    }
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (f) =>
          f.title.toLowerCase().includes(q) ||
          f.xml_url.toLowerCase().includes(q) ||
          f.detail.toLowerCase().includes(q)
      );
    }
    return {
      summary: feedData.summary,
      categories: feedData.categories,
      feeds: list,
    };
  },

  // 6. Pipeline Run History & Trigger
  async getPipelineRuns() {
    return pipelineRuns;
  },

  async triggerPipelineRun(type = "Orchestrated End-to-End", options = {}) {
    const newRun = {
      run_id: `RUN-${new Date().toISOString().replace(/[-:T.]/g, "").slice(0, 14)}`,
      timestamp: new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST",
      type: type,
      status: "SUCCESS",
      duration_seconds: 124,
      stages_executed: [
        "Stage 1: RSS Ingestion",
        "Stage 2: Link Dedup",
        "Stage 3: AI Triage",
        "Stage 4: Morning Brief",
      ],
      items_extracted: 356,
      items_deduped: 134,
      items_triaged: 49,
      draft_items_produced: 4,
      draft_file: "Output_Mail_Draft/Live_Update.htm",
      processor: options.processor || "base",
    };
    pipelineRuns.unshift(newRun);
    return newRun;
  },

  // 7. Settings
  async getSettings() {
    return JSON.parse(JSON.stringify(mailSettings));
  },

  async saveSettings(updated) {
    mailSettings = { ...mailSettings, ...updated };
    return mailSettings;
  },
};
