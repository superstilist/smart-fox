/* ── Theme Toggle ── */
(function initTheme() {
  const stored = localStorage.getItem("theme");
  if (stored) {
    document.documentElement.setAttribute("data-theme", stored);
  } else {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "dark");
  }
})();

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
      toggle.style.transform = "scale(0.9)";
      setTimeout(() => { toggle.style.transform = ""; }, 150);
    });
  }
});

const form = document.querySelector("[data-loading-form]");
const loading = document.querySelector("[data-loading]");

let currentTaskId = null;
let currentEventSource = null;
let loadingTimeout = null;
let _pollGen = 0;

function showLoading(message) {
  if (!loading) return;
  const title = loading.querySelector(".loading-title");
  if (title) title.textContent = message || "Searching...";
  loading.hidden = false;
  if (loadingTimeout) clearTimeout(loadingTimeout);
  loadingTimeout = setTimeout(() => hideLoading(), 60000);
}

function hideLoading() {
  if (loading) loading.hidden = true;
  if (loadingTimeout) { clearTimeout(loadingTimeout); loadingTimeout = null; }
}

function showToast(message) {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2500);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

function buildRecCard(item) {
  const genres = (item.genres || []).slice(0, 4).map(g => `<span>${g}</span>`).join("");
  const url = item.url ? `<a class="rec-link" href="${item.url}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">MAL</a>` : "";
  const escapedTitle = (item.title || "").replace(/'/g, "\\'").replace(/"/g, "&quot;");
  const matchReason = item.match_reason ? `<div class="rec-match-reason">${escapeHtml(item.match_reason)}</div>` : "";
  const weightedScore = item.weighted_score ? `<div class="rec-weighted-score">Weighted: ${item.weighted_score}</div>` : "";
  const posterSrc = item.poster || "";
  const srcAttr = posterSrc ? `src="${posterSrc}"` : "";
  return `
    <div class="ai-rec-card" data-title="${item.title || ""}" data-rank="${item.rank || 0}" data-poster="${posterSrc}" onclick="showDetail('${escapedTitle}')">
      <div class="rec-poster-wrap">
        <img class="rec-poster" ${srcAttr} alt="${item.title || ""}" loading="lazy" onerror="this.removeAttribute('src')">
        <div class="rec-poster-placeholder"><span>#${item.rank || "?"}</span></div>
        <div class="rec-rating-badge">${item.rating || "~"}</div>
      </div>
      <div class="rec-body">
        <div class="rec-head">
          <strong>${item.title || "Unknown"}</strong>
          <em>${item.similarity_percentage || 0}%</em>
        </div>
        ${matchReason}
        <p class="rec-synopsis">${item.synopsis || item.overall_explanation || ""}</p>
        ${genres ? `<div class="rec-genres">${genres}</div>` : ""}
        <div class="bar"><span style="width: ${item.similarity_percentage || 0}%"></span></div>
        ${weightedScore}
        <div class="mini-metrics">
          <span title="Story similarity">Story ${item.story_similarity || 0}</span>
          <span title="Character similarity">Chars ${item.character_similarity || 0}</span>
          <span title="World similarity">World ${item.world_similarity || 0}</span>
          <span title="Theme similarity">Themes ${item.theme_similarity || 0}</span>
          <span title="Power system similarity">Power ${item.power_system_similarity || 0}</span>
          <span title="Emotional similarity">Emotion ${item.emotional_similarity || 0}</span>
          <span title="Art style similarity">Art ${item.art_style_similarity || 0}</span>
          <span title="Music similarity">Music ${item.music_similarity || 0}</span>
          <span title="Pacing similarity">Pacing ${item.pacing_similarity || 0}</span>
          <span title="Tone similarity">Tone ${item.tone_similarity || 0}</span>
          <span title="Audience similarity">Audience ${item.audience_similarity || 0}</span>
          <span title="Genre blend similarity">Genre ${item.genre_blend_similarity || 0}</span>
          <span title="Confidence score">Confidence ${item.confidence_score || 0}</span>
        </div>
        <div class="rec-actions">
          ${url}
          <button class="text-btn" style="height:32px;padding:0 10px;font-size:0.76rem;" onclick="event.stopPropagation(); shareSingleRec('${escapedTitle}')" title="Share">Share</button>
        </div>
      </div>
    </div>`;
}

function clearAll() {
  _pollGen++;
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  currentTaskId = null;

  const main = document.querySelector(".md-container");
  if (main) {
    main.querySelectorAll("section, .profile-layout, article, .md-dialog-scrim").forEach(el => {
      if (!el.closest(".search-section") && !el.closest(".md-dialog-scrim")) {
        el.remove();
      }
    });
  }
}

function cancelSearch() {
  if (!currentTaskId) return;
  fetch("/api/ai/cancel/" + currentTaskId, { method: "POST" })
    .then(r => r.json())
    .then(data => {
      if (data.status === "cancelled") {
        showToast("Search cancelled");
        if (currentEventSource) {
          currentEventSource.close();
          currentEventSource = null;
        }
        updateStatus(100, "Cancelled", "");
        hideCancelBtn();
        hideLoading();
      }
    })
    .catch(() => {});
}

function showCancelBtn() {
  const btn = document.getElementById("cancel-search-btn");
  if (btn) btn.style.display = "";
}

function hideCancelBtn() {
  const btn = document.getElementById("cancel-search-btn");
  if (btn) btn.style.display = "none";
}

function buildResultSection() {
  const main = document.querySelector(".md-container");
  if (!main || document.getElementById("ai-status-panel")) return;

  main.insertAdjacentHTML("beforeend", `
    <section class="profile-layout" style="margin-top: 24px;">
      <div class="profile-grid">
        <div class="profile-main" style="grid-column: 1 / -1;">
          <div class="md-card md-card-filled wide" id="ai-status-panel">
            <h3 class="md-typescale-title-large">AI Search Status</h3>
            <div class="ai-status-box">
              <div class="status-header">
                <div class="status-text md-typescale-body-medium" id="ai-status-text">Initializing...</div>
                <div class="status-count md-typescale-label-large" id="ai-status-count">0 found</div>
              </div>
              <div class="md-linear-progress">
                <div class="md-linear-progress-bar" id="ai-status-bar" style="width: 0%"></div>
              </div>
              <div class="status-detail md-typescale-body-small md-color-muted" id="ai-status-detail"></div>
              <button class="md-btn md-btn-text" id="cancel-search-btn" type="button" style="display:none;">Cancel</button>
            </div>
          </div>
          <div class="md-card md-card-elevated wide" id="ai-recommendations">
            <div class="rec-header">
              <h3 class="md-typescale-title-large" id="ai-rec-title">AI Recommendations <span id="rec-total-count" class="rec-count md-color-muted"></span></h3>
              <button class="md-icon-btn" onclick="shareRecommendations()" title="Share">
                <span class="material-symbols-rounded">share</span>
              </button>
            </div>
            <p class="md-typescale-body-small md-color-muted rec-hint">Click any card to see full anime details</p>
            <div class="ai-rec-grid" id="rec-grid"></div>
          </div>
        </div>
      </div>
    </section>`);
}

function startSearch(query, description, contentFilter, negativePrompt) {
  clearAll();
  buildResultSection();

  const recGrid = document.getElementById("rec-grid");
  if (recGrid) recGrid.innerHTML = "";

  updateStatus(0, "Searching...", "");
  showCancelBtn();
  showLoading("Searching anime databases...");

  fetch("/api/ai/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, description, content_filter: contentFilter, negative_prompt: negativePrompt }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        hideLoading();
        updateStatus(100, "Error: " + data.error, "");
        return;
      }
      currentTaskId = data.task_id;
      hideLoading();
      listenToStream(currentTaskId);
    })
    .catch(err => {
      hideLoading();
      updateStatus(100, "Failed: " + err.message, "");
    });
}

function listenToStream(taskId) {
  if (currentEventSource) {
    currentEventSource.close();
  }

  const es = new EventSource("/api/ai/stream/" + taskId);
  currentEventSource = es;

  es.onmessage = function (event) {
    try {
      const data = JSON.parse(event.data);
      updateStatus(data.progress || 0, data.message || "", data.count > 0 ? `${data.count} found` : "");

      if (data.commentary && Array.isArray(data.commentary)) {
        updateCommentary(data.commentary);
      }

      if (data.latest) {
        appendRecommendation(data.latest);
      }

      if (data.status === "done" || data.status === "error" || data.status === "cancelled") {
        es.close();
        currentEventSource = null;
        hideCancelBtn();
        if (data.status === "done") {
          if (data.recommendation && data.recommendation.top_50 && data.recommendation.top_50.length) {
            const recGrid = document.getElementById("rec-grid");
            if (recGrid) {
              recGrid.innerHTML = "";
              recGrid.dataset.aiRawText = data.recommendation.ai_raw_text || "";
              recGrid.dataset.sourceTitle = data.recommendation.source_title || "";
              data.recommendation.top_50.forEach(item => {
                recGrid.innerHTML += buildRecCard(item);
              });
              fetchPostersForGrid();
              updateRecCount();
            }
          }
          hideLoading();
        } else {
          hideLoading();
        }
      }
    } catch (e) {
    }
  };

  es.onerror = function () {
    es.close();
    currentEventSource = null;
    setTimeout(() => pollTaskStatus(taskId, _pollGen), 1000);
  };
}

function updateCommentary(lines) {
  const container = document.getElementById("commentary-lines");
  if (!container) return;
  container.innerHTML = "";
  const recent = lines.slice(-15);
  for (const line of recent) {
    const div = document.createElement("div");
    div.className = "commentary-line";
    div.textContent = line.replace(/^##\s*/, "");
    container.appendChild(div);
  }
  container.scrollTop = container.scrollHeight;
}

function pollTaskStatus(taskId, gen) {
  if (gen !== undefined && gen !== _pollGen) return;
  fetch("/api/ai/status/" + taskId)
    .then(r => r.json())
    .then(data => {
      updateStatus(data.progress || 0, data.message || "", (data.results || []).length > 0 ? `${data.results.length} found` : "");

      if (data.status === "done" || data.status === "error") {
        hideLoading();
        if (data.status === "done") loadFullResults(taskId);
        return;
      }
      setTimeout(() => pollTaskStatus(taskId, gen), 500);
    })
    .catch(() => {
      setTimeout(() => pollTaskStatus(taskId, gen), 1000);
    });
}

function loadFullResults(taskId) {
  hideLoading();
  fetch("/api/ai/status/" + taskId)
    .then(r => r.json())
    .then(data => {
      const recGrid = document.getElementById("rec-grid");
      if (!recGrid) return;

      let items = [];
      if (data.recommendation) {
        items = data.recommendation.top_50 || data.recommendation.top_25 || [];
        recGrid.dataset.aiRawText = data.recommendation.ai_raw_text || "";
        recGrid.dataset.sourceTitle = data.recommendation.source_title || "";
      } else if (data.results && data.results.length) {
        items = data.results;
      }

      if (items.length > 0) {
        recGrid.innerHTML = "";
        items.forEach(item => {
          recGrid.innerHTML += buildRecCard(item);
        });
      } else if (!recGrid.children.length) {
        recGrid.innerHTML = '<p class="rec-hint">No recommendations found. Try a different search.</p>';
      }

      fetchPostersForGrid();
      updateRecCount();
    })
    .catch(() => {});
}

function updateStatus(progress, message, countText) {
  const bar = document.getElementById("ai-status-bar");
  const text = document.getElementById("ai-status-text");
  const detail = document.getElementById("ai-status-detail");
  const count = document.getElementById("ai-status-count");
  if (bar) bar.style.width = progress + "%";
  if (text) text.textContent = message;
  if (detail) detail.textContent = `Progress: ${progress}%`;
  if (count) count.textContent = countText;
}

function appendRecommendation(item) {
  const grid = document.getElementById("rec-grid");
  if (!grid) return;
  grid.insertAdjacentHTML("beforeend", buildRecCard(item));
  const lastCard = grid.lastElementChild;
  if (lastCard) fetchPosterForCard(lastCard);
  updateRecCount();
}

function updateRecCount() {
  const grid = document.getElementById("rec-grid");
  const count = document.getElementById("rec-total-count");
  if (grid && count) {
    const total = grid.querySelectorAll(".ai-rec-card").length;
    count.textContent = `${total} recommendation${total !== 1 ? "s" : ""} total`;
  }
}

function fetchPostersForGrid() {
  const cards = document.querySelectorAll("#rec-grid .ai-rec-card[data-title]");
  if (!cards.length) return;
  const needPoster = Array.from(cards).filter(c => !c.dataset.poster);
  if (!needPoster.length) return;
  const titles = needPoster.map(c => c.dataset.title).filter(Boolean);
  if (!titles.length) return;

  const contentFilter = document.getElementById("content_filter")?.value || "sfw";
  fetch("/api/recommend/posters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles, content_filter: contentFilter }),
  })
    .then(r => r.ok ? r.json() : {})
    .then(data => {
      needPoster.forEach(card => applyPosterData(card, data[card.dataset.title]));
    })
    .catch(() => {});
}

function fetchPosterForCard(card) {
  const title = card.dataset.title;
  if (!title) return;
  const contentFilter = document.getElementById("content_filter")?.value || "sfw";
  fetch("/api/recommend/posters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles: [title], content_filter: contentFilter }),
  })
    .then(r => r.ok ? r.json() : {})
    .then(data => applyPosterData(card, data[title]))
    .catch(() => {});
}

function applyPosterData(card, info) {
  if (!info) return;
  const posterImg = card.querySelector(".rec-poster");
  if (posterImg && info.poster) {
    posterImg.src = info.poster;
    posterImg.alt = card.dataset.title + " poster";
  }
  const badge = card.querySelector(".rec-rating-badge");
  if (badge && info.score) badge.textContent = info.score;
  const syn = card.querySelector(".rec-synopsis");
  if (syn && info.synopsis && !syn.textContent.trim()) syn.textContent = info.synopsis;
  const genres = card.querySelector(".rec-genres");
  if (genres && info.genres && info.genres.length && !genres.children.length) {
    info.genres.slice(0, 4).forEach(g => {
      const s = document.createElement("span");
      s.textContent = g;
      genres.appendChild(s);
    });
  }
}

function showDetail(title) {
  const modal = document.getElementById("detail-modal");
  const loadingEl = document.getElementById("detail-loading");
  const content = document.getElementById("detail-content");
  if (!modal) return;

  modal.style.display = "flex";
  loadingEl.style.display = "flex";
  content.style.display = "none";

  const contentFilter = document.getElementById("content_filter")?.value || "sfw";
  fetch("/api/anime/detail?title=" + encodeURIComponent(title) + "&content_filter=" + encodeURIComponent(contentFilter))
    .then(r => r.json())
    .then(data => {
      loadingEl.style.display = "none";
      content.style.display = "block";
      if (data.error) {
        content.innerHTML = `<div class="detail-error">${data.error}</div>`;
        return;
      }
      content.innerHTML = buildDetailHTML(data);
    })
    .catch(err => {
      loadingEl.style.display = "none";
      content.style.display = "block";
      content.innerHTML = `<div class="detail-error">Failed to load: ${err.message}</div>`;
    });
}

function closeDetail() {
  const modal = document.getElementById("detail-modal");
  if (modal) modal.style.display = "none";
}

function buildDetailHTML(d) {
  const genres = (d.genres || []).map(g => `<span class="md-chip md-chip-primary">${g}</span>`).join("");
  const themes = (d.themes || []).map(t => `<span class="md-chip md-chip-secondary">${t}</span>`).join("");
  const studios = (d.studios || []).join(", ") || "Unknown";
  const producers = (d.producers || []).join(", ") || "Unknown";

  return `
    <div style="display: flex; gap: 24px; margin-bottom: 24px;">
      ${d.poster ? `<img src="${d.poster}" alt="${d.title}" style="width: 150px; border-radius: var(--md-shape-md); flex-shrink: 0; object-fit: cover;">` : ""}
      <div style="display: flex; flex-direction: column; gap: 8px;">
        <h2 class="md-typescale-headline-small">${d.title || "Unknown"}</h2>
        ${d.title_english ? `<p class="md-typescale-body-large md-color-muted">${d.title_english}</p>` : ""}
        ${d.title_japanese ? `<p class="md-typescale-body-large md-color-muted">${d.title_japanese}</p>` : ""}
        <div class="chips-container" style="margin-top: 8px;">${genres}${themes}</div>
      </div>
    </div>
    
    ${d.synopsis ? `<div class="details-card md-card md-card-filled" style="margin-bottom: 16px;"><h3 class="md-typescale-title-large">Synopsis</h3><p class="md-typescale-body-medium">${d.synopsis}</p></div>` : ""}
    ${d.background ? `<div class="details-card md-card md-card-filled" style="margin-bottom: 16px;"><h3 class="md-typescale-title-large">Background</h3><p class="md-typescale-body-medium">${d.background}</p></div>` : ""}
    
    <div class="details-card md-card md-card-filled" style="margin-bottom: 16px;">
      <h3 class="md-typescale-title-large">Information</h3>
      <div class="info-grid">
        <div class="info-item"><span class="info-label">Score</span><span class="info-value"><span class="material-symbols-rounded star-icon">star</span>${d.score || "N/A"}</span></div>
        <div class="info-item"><span class="info-label">Ranked</span><span class="info-value">#${d.rank || "N/A"}</span></div>
        <div class="info-item"><span class="info-label">Popularity</span><span class="info-value">#${d.popularity || "N/A"}</span></div>
        <div class="info-item"><span class="info-label">Episodes</span><span class="info-value">${d.episodes || "Unknown"}</span></div>
        <div class="info-item"><span class="info-label">Status</span><span class="info-value">${d.status || "Unknown"}</span></div>
        <div class="info-item"><span class="info-label">Type</span><span class="info-value">${d.type || "Unknown"}</span></div>
        <div class="info-item"><span class="info-label">Source</span><span class="info-value">${d.source || "Unknown"}</span></div>
        <div class="info-item"><span class="info-label">Duration</span><span class="info-value">${d.duration || "Unknown"}</span></div>
        <div class="info-item"><span class="info-label">Rating</span><span class="info-value">${d.rating_val || d.rating || "Unknown"}</span></div>
        <div class="info-item"><span class="info-label">Scored By</span><span class="info-value">${d.scored_by ? d.scored_by.toLocaleString() : "N/A"}</span></div>
        <div class="info-item"><span class="info-label">Members</span><span class="info-value">${d.members ? d.members.toLocaleString() : "N/A"}</span></div>
        <div class="info-item"><span class="info-label">Favorites</span><span class="info-value">${d.favorites ? d.favorites.toLocaleString() : "N/A"}</span></div>
      </div>
    </div>
    
    <div class="details-card md-card md-card-filled" style="margin-bottom: 16px;">
      <h3 class="md-typescale-title-large">Production</h3>
      <div class="info-grid">
        <div class="info-item"><span class="info-label">Studios</span><span class="info-value">${studios}</span></div>
        <div class="info-item"><span class="info-label">Producers</span><span class="info-value">${producers}</span></div>
        ${d.aired ? `<div class="info-item full-width"><span class="info-label">Aired</span><span class="info-value">${d.aired}</span></div>` : ""}
      </div>
    </div>
    
    <div class="md-dialog-actions" style="padding: 0; justify-content: flex-start; margin-top: 16px;">
      ${d.url ? `<a class="md-btn md-btn-filled" href="${d.url}" target="_blank" rel="noreferrer">View on MyAnimeList</a>` : ""}
      ${d.trailer ? `<a class="md-btn md-btn-outlined" href="${d.trailer}" target="_blank" rel="noreferrer"><span class="material-symbols-rounded">play_arrow</span> Trailer</a>` : ""}
    </div>
  `;
}

function shareRecommendations() {
  const grid = document.getElementById("rec-grid");
  if (!grid) return;
  const src = grid.dataset.sourceTitle || "Anime";
  const cards = grid.querySelectorAll(".ai-rec-card");
  if (!cards.length) return;
  const lines = [`Recommendations for ${src}:\n`];
  cards.forEach(c => {
    const em = c.querySelector(".rec-head em");
    lines.push(`#${c.dataset.rank} ${c.dataset.title} (${em ? em.textContent : ""})`);
  });
  const text = lines.join("\n");
  if (navigator.share) {
    navigator.share({ title: `Recommendations for ${src}`, text });
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard!"));
  }
}

function shareSingleRec(title) {
  const text = `Check out "${title}" - recommended by AI Anime Search`;
  if (navigator.share) {
    navigator.share({ title: `Recommendation: ${title}`, text });
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard!"));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const searchForm = document.querySelector("[data-loading-form]");

  if (searchForm) {
    searchForm.addEventListener("submit", function (e) {
      e.preventDefault();

      const query = document.getElementById("query").value.trim();
      const description = document.getElementById("description").value.trim();
      const contentFilter = document.getElementById("content_filter").value;
      const negativePrompt = document.getElementById("negative_prompt").value.trim();

      if (!query && !description) {
        showToast("Enter an anime title or description");
        return;
      }

      startSearch(query, description, contentFilter, negativePrompt);
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDetail();
  });
});

/* ── Settings ── */
function openSettings() {
  const modal = document.getElementById("settings-modal");
  if (!modal) return;
  modal.style.display = "flex";
  loadSettingsForm();
  updateTokenUsage();
  if (!window._tokenPollInterval) {
    window._tokenPollInterval = setInterval(updateTokenUsage, 3000);
  }
}
function closeSettings() {
  const modal = document.getElementById("settings-modal");
  if (modal) modal.style.display = "none";
  if (window._tokenPollInterval) {
    clearInterval(window._tokenPollInterval);
    window._tokenPollInterval = null;
  }
}

function loadSettingsForm() {
  fetch("/api/config").then(r => r.json()).then(cfg => {
    setVal("cfg-local-base-url", cfg.local_ai_base_url);
    setVal("cfg-local-model", cfg.local_ai_model);
    setVal("cfg-local-api-key", cfg.has_local_api_key ? "••••••••" : "");
    setVal("cfg-openrouter-api-key", cfg.has_api_key ? "••••••••" : "");
    setVal("cfg-openrouter-model", cfg.openrouter_model);
    setVal("cfg-openrouter-base-url", cfg.ai_base_url);
    setVal("cfg-openrouter-fallback", cfg.openrouter_fallback_models);
    setVal("cfg-ai-temperature", cfg.ai_temperature);
    setVal("cfg-ai-max-tokens", cfg.ai_max_tokens);
    setVal("cfg-ai-timeout", cfg.ai_timeout_seconds);
    setVal("cfg-token-budget", cfg.token_budget);
    setVal("cfg-agent-max-iterations", cfg.agent_max_iterations);
    setVal("cfg-agent-max-tool-calls", cfg.agent_max_tool_calls);

    const provider = cfg.ai_provider || "local";
    document.querySelectorAll('input[name="ai_provider"]').forEach(r => {
      r.checked = r.value === provider;
      r.closest(".provider-card")?.classList.toggle("active", r.checked);
    });
    toggleProviderSections(provider);
  }).catch(() => {});
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el && val !== undefined && val !== null) el.value = val;
}

function toggleProviderSections(provider) {
  const localSection = document.getElementById("settings-local-section");
  const orSection = document.getElementById("settings-openrouter-section");
  const modelStatus = document.getElementById("settings-model-status-section");
  if (localSection) localSection.style.display = provider === "local" ? "" : "none";
  if (orSection) orSection.style.display = provider === "openrouter" ? "" : "none";
  if (modelStatus) modelStatus.style.display = provider === "openrouter" ? "" : "none";
}

function collectSettings() {
  const provider = document.querySelector('input[name="ai_provider"]:checked')?.value || "local";
  return {
    ai_provider: provider,
    local_ai_base_url: getVal("cfg-local-base-url"),
    local_ai_model: getVal("cfg-local-model"),
    local_ai_api_key: getVal("cfg-local-api-key"),
    ai_api_key: getVal("cfg-openrouter-api-key"),
    openrouter_model: getVal("cfg-openrouter-model"),
    ai_base_url: getVal("cfg-openrouter-base-url"),
    openrouter_fallback_models: getVal("cfg-openrouter-fallback"),
    ai_temperature: parseFloat(getVal("cfg-ai-temperature")) || 0.15,
    ai_max_tokens: parseInt(getVal("cfg-ai-max-tokens")) || 4096,
    ai_timeout_seconds: parseFloat(getVal("cfg-ai-timeout")) || 120,
    token_budget: parseInt(getVal("cfg-token-budget")) || 100000,
    agent_max_iterations: parseInt(getVal("cfg-agent-max-iterations")) || 10,
    agent_max_tool_calls: parseInt(getVal("cfg-agent-max-tool-calls")) || 15,
  };
}

function getVal(id) {
  return document.getElementById(id)?.value || "";
}

function saveSettings() {
  const status = document.getElementById("settings-status");
  const payload = collectSettings();
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(r => r.json()).then(data => {
    if (data.error) {
      if (status) { status.textContent = "Error: " + data.error; status.className = "settings-status error"; }
    } else {
      if (status) { status.textContent = "Saved!"; status.className = "settings-status ok"; }
      updateTokenUsage();
      setTimeout(() => { if (status) status.textContent = ""; }, 2000);
    }
  }).catch(err => {
    if (status) { status.textContent = "Save failed: " + err; status.className = "settings-status error"; }
  });
}

function updateTokenUsage() {
  fetch("/api/tokens/usage").then(r => r.json()).then(data => {
    const textEl = document.getElementById("token-usage-text");
    const pctEl = document.getElementById("token-usage-pct");
    const fillEl = document.getElementById("token-usage-fill");
    if (!textEl) return;
    const total = data.total_tokens || 0;
    const calls = data.calls || 0;
    const budget = data.budget || 100000;
    const pct = data.budget_used_pct || 0;
    textEl.textContent = `${total.toLocaleString()} tokens used (${calls} calls)`;
    pctEl.textContent = `${pct}%`;
    if (fillEl) {
      fillEl.style.width = `${Math.min(pct, 100)}%`;
      fillEl.className = "token-usage-fill";
      if (pct >= 100) fillEl.classList.add("over-budget");
      else if (pct >= 80) fillEl.classList.add("near-budget");
      else fillEl.classList.add("ok");
    }
  }).catch(() => {});
}

function testConnection() {
  const status = document.getElementById("settings-status");
  if (status) { status.textContent = "Testing connection..."; status.className = "settings-status"; }
  fetch("/api/test-connection", { method: "POST" }).then(r => r.json()).then(data => {
    if (data.status === 200) {
      if (status) { status.textContent = "Connection OK (200)"; status.className = "settings-status ok"; }
    } else {
      if (status) { status.textContent = "Failed (" + (data.status || "error") + "): " + (data.error || data.body || ""); status.className = "settings-status error"; }
    }
  }).catch(err => {
    if (status) { status.textContent = "Test failed: " + err; status.className = "settings-status error"; }
  });
}

function resetDefaults() {
  if (!confirm("Reset all settings to defaults?")) return;
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }).then(r => r.json()).then(() => {
    loadSettingsForm();
    showToast("Settings reset to defaults");
  });
}

function refreshModelStatus() {
  const grid = document.getElementById("model-status-grid");
  if (!grid) return;
  grid.innerHTML = '<div class="model-status-loading">Checking models...</div>';
  fetch("/api/models/status").then(r => r.json()).then(data => {
    if (data.error) {
      grid.innerHTML = `<div class="model-status-error">${escapeHtml(data.error)}</div>`;
      return;
    }
    const models = data.models || [];
    if (!models.length) {
      grid.innerHTML = '<div class="model-status-loading">No models configured</div>';
      return;
    }
    grid.innerHTML = models.map(m => {
      const statusClass = m.status === "ok" ? "online" : m.status === "rate_limited" ? "limited" : "offline";
      const statusLabel = m.status === "ok" ? "Available" : m.status === "rate_limited" ? "Rate Limited" : "Error";
      const latency = m.latency_ms ? `${m.latency_ms}ms` : "";
      const badge = m.is_primary ? '<span class="model-badge-primary">Primary</span>' : "";
      const errorInfo = m.error ? `<span class="model-error-detail" title="${escapeHtml(m.error)}">!</span>` : "";
      return `<div class="model-status-card ${statusClass}">
        <div class="model-status-dot"></div>
        <div class="model-status-info">
          <div class="model-status-name">${escapeHtml(m.model)} ${badge}</div>
          <div class="model-status-meta">${statusLabel} ${latency} ${errorInfo}</div>
        </div>
      </div>`;
    }).join("");
  }).catch(err => {
    grid.innerHTML = `<div class="model-status-error">Failed to check models: ${escapeHtml(String(err))}</div>`;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const gear = document.getElementById("settings-toggle");
  if (gear) gear.addEventListener("click", openSettings);

  document.querySelectorAll('input[name="ai_provider"]').forEach(radio => {
    radio.addEventListener("change", () => {
      document.querySelectorAll(".provider-card").forEach(c => c.classList.remove("active"));
      radio.closest(".provider-card")?.classList.add("active");
      toggleProviderSections(radio.value);
      saveSettings();
    });
  });

  const testBtn = document.getElementById("settings-test");
  if (testBtn) testBtn.addEventListener("click", testConnection);

  const resetBtn = document.getElementById("settings-reset");
  if (resetBtn) resetBtn.addEventListener("click", resetDefaults);

  const refreshBtn = document.getElementById("settings-refresh-models");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshModelStatus);

  let saveTimeout;
  function onSettingsChange() {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => saveSettings(), 500);
  }

  const settingsModal = document.getElementById("settings-modal");
  if (settingsModal) {
    settingsModal.addEventListener("input", onSettingsChange);
    settingsModal.addEventListener("change", onSettingsChange);
  }
});

/* ── Cancel Button ── */
document.addEventListener("DOMContentLoaded", () => {
  const cancelBtn = document.getElementById("cancel-search-btn");
  if (cancelBtn) cancelBtn.addEventListener("click", cancelSearch);
});


