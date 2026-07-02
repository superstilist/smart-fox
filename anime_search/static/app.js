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
  return `
    <div class="ai-rec-card" data-title="${item.title || ""}" data-rank="${item.rank || 0}" onclick="showDetail('${escapedTitle}')">
      <div class="rec-poster-wrap">
        <img class="rec-poster" src="" alt="${item.title || ""}" loading="lazy">
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
          <button class="rec-share-btn" onclick="event.stopPropagation(); shareSingleRec('${escapedTitle}')" title="Share">Share</button>
        </div>
      </div>
    </div>`;
}

function clearAll() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  currentTaskId = null;

  const main = document.querySelector(".shell");
  if (main) {
    main.querySelectorAll("section, article").forEach(el => {
      if (!el.closest(".search-band") && !el.closest(".detail-modal")) {
        el.remove();
      }
    });
  }
}

function buildResultSection() {
  const main = document.querySelector(".shell");
  if (!main || document.getElementById("ai-status-panel")) return;

  main.insertAdjacentHTML("beforeend", `
    <section class="content-grid">
      <article class="panel wide" id="ai-status-panel">
        <h3>AI Search Status</h3>
        <div class="ai-status-box">
          <div class="status-header">
            <div class="status-text" id="ai-status-text">Initializing...</div>
            <div class="status-count" id="ai-status-count">0 found</div>
          </div>
          <div class="status-bar-wrap">
            <div class="status-bar" id="ai-status-bar" style="width: 0%"></div>
          </div>
          <div class="status-detail" id="ai-status-detail"></div>
        </div>
      </article>
      <article class="panel wide" id="ai-recommendations">
        <div class="rec-section-header">
          <h3 id="ai-rec-title">Recommendations <span id="rec-total-count" class="rec-count"></span></h3>
          <button class="share-btn" onclick="shareRecommendations()" title="Share">Share</button>
        </div>
        <p class="rec-hint">Click any card to see full anime details</p>
        <div class="ai-rec-grid" id="rec-grid"></div>
      </article>
    </section>`);
}

function startSearch(query, description, contentFilter, negativePrompt) {
  clearAll();
  buildResultSection();

  const recGrid = document.getElementById("rec-grid");
  if (recGrid) recGrid.innerHTML = "";

  updateStatus(0, "Searching...", "");
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

      if (data.latest) {
        appendRecommendation(data.latest);
      }

      if (data.status === "done" || data.status === "error") {
        es.close();
        currentEventSource = null;
        if (data.status === "done") {
          loadFullResults(taskId);
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
    setTimeout(() => pollTaskStatus(taskId), 1000);
  };
}

function pollTaskStatus(taskId) {
  fetch("/api/ai/status/" + taskId)
    .then(r => r.json())
    .then(data => {
      updateStatus(data.progress || 0, data.message || "", (data.results || []).length > 0 ? `${data.results.length} found` : "");

      if (data.status === "done" || data.status === "error") {
        hideLoading();
        if (data.status === "done") loadFullResults(taskId);
        return;
      }
      setTimeout(() => pollTaskStatus(taskId), 500);
    })
    .catch(() => {
      setTimeout(() => pollTaskStatus(taskId), 1000);
    });
}

function loadFullResults(taskId) {
  hideLoading();
  fetch("/api/ai/status/" + taskId)
    .then(r => r.json())
    .then(data => {
      const recGrid = document.getElementById("rec-grid");
      if (!recGrid) return;

      if (data.recommendation) {
        const items = data.recommendation.top_50 || data.recommendation.top_25 || [];
        recGrid.innerHTML = "";
        recGrid.dataset.aiRawText = data.recommendation.ai_raw_text || "";
        recGrid.dataset.sourceTitle = data.recommendation.source_title || "";
        items.forEach(item => {
          recGrid.innerHTML += buildRecCard(item);
        });
      } else if (data.results && data.results.length) {
        recGrid.innerHTML = "";
        data.results.forEach(item => {
          recGrid.innerHTML += buildRecCard(item);
        });
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
  const titles = Array.from(cards).map(c => c.dataset.title).filter(Boolean);
  if (!titles.length) return;

  fetch("/api/recommend/posters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles }),
  })
    .then(r => r.ok ? r.json() : {})
    .then(data => {
      cards.forEach(card => applyPosterData(card, data[card.dataset.title]));
    })
    .catch(() => {});
}

function fetchPosterForCard(card) {
  const title = card.dataset.title;
  if (!title) return;
  fetch("/api/recommend/posters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles: [title] }),
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

  fetch("/api/anime/detail?title=" + encodeURIComponent(title))
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
  const genres = (d.genres || []).map(g => `<span>${g}</span>`).join("");
  const themes = (d.themes || []).map(t => `<span class="theme">${t}</span>`).join("");
  const studios = (d.studios || []).join(", ") || "Unknown";
  const producers = (d.producers || []).join(", ") || "Unknown";

  return `
    <div class="detail-header">
      ${d.poster ? `<img class="detail-poster" src="${d.poster}" alt="${d.title}">` : ""}
      <div class="detail-info">
        <h2>${d.title || "Unknown"}</h2>
        ${d.title_english ? `<p class="detail-alt-title">${d.title_english}</p>` : ""}
        ${d.title_japanese ? `<p class="detail-alt-title jp">${d.title_japanese}</p>` : ""}
        <div class="detail-tags">${genres}${themes}</div>
      </div>
    </div>
    ${d.synopsis ? `<div class="detail-section"><h4>Synopsis</h4><p>${d.synopsis}</p></div>` : ""}
    ${d.background ? `<div class="detail-section"><h4>Background</h4><p>${d.background}</p></div>` : ""}
    <div class="detail-grid">
      <div class="detail-stat"><span>Score</span><strong>${d.score || "N/A"}</strong></div>
      <div class="detail-stat"><span>Ranked</span><strong>#${d.rank || "N/A"}</strong></div>
      <div class="detail-stat"><span>Popularity</span><strong>#${d.popularity || "N/A"}</strong></div>
      <div class="detail-stat"><span>Episodes</span><strong>${d.episodes || "Unknown"}</strong></div>
      <div class="detail-stat"><span>Status</span><strong>${d.status || "Unknown"}</strong></div>
      <div class="detail-stat"><span>Type</span><strong>${d.type || "Unknown"}</strong></div>
      <div class="detail-stat"><span>Source</span><strong>${d.source || "Unknown"}</strong></div>
      <div class="detail-stat"><span>Duration</span><strong>${d.duration || "Unknown"}</strong></div>
      <div class="detail-stat"><span>Rating</span><strong>${d.rating_val || d.rating || "Unknown"}</strong></div>
      <div class="detail-stat"><span>Scored By</span><strong>${d.scored_by ? d.scored_by.toLocaleString() : "N/A"}</strong></div>
      <div class="detail-stat"><span>Members</span><strong>${d.members ? d.members.toLocaleString() : "N/A"}</strong></div>
      <div class="detail-stat"><span>Favorites</span><strong>${d.favorites ? d.favorites.toLocaleString() : "N/A"}</strong></div>
    </div>
    <div class="detail-section">
      <h4>Studios</h4><p>${studios}</p>
    </div>
    <div class="detail-section">
      <h4>Producers</h4><p>${producers}</p>
    </div>
    ${d.aired ? `<div class="detail-section"><h4>Aired</h4><p>${d.aired}</p></div>` : ""}
    <div class="detail-actions">
      ${d.url ? `<a class="detail-link" href="${d.url}" target="_blank" rel="noreferrer">View on MyAnimeList</a>` : ""}
      ${d.trailer ? `<a class="detail-link trailer" href="${d.trailer}" target="_blank" rel="noreferrer">Watch Trailer</a>` : ""}
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
