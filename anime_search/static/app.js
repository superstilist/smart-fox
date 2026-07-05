/* ════════════════════════════════════════════════════════════
   Smart Fox — Application Script
   Organized into a single IIFE with a delegated event dispatcher.
   ════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  /* ── State ── */
  const state = {
    currentTaskId: null,
    currentEventSource: null,
    loadingTimeout: null,
    pollGen: 0,
    searchInProgress: false,
    focusReturnEl: null, // element to refocus when a dialog closes
  };

  // Library state
  let _libraryEntries = [];
  let _libraryFilter = "";
  let _libraryEditId = null;
  let _librarySearchTimeout = null;
  let _librarySort = "added_at";
  let _librarySortDesc = true;

  /* ── AniList Cache ── */
  const CACHE_KEY = "smartfox_anilist_cache";
  const CACHE_TTL = 7 * 24 * 60 * 60 * 1000; // 7 days

  function getCache() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) { return {}; }
  }

  function setCache(cache) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
    } catch (e) { logError("setCache", e); }
  }

  function getCachedAnime(title) {
    const cache = getCache();
    const key = title.toLowerCase().trim();
    const entry = cache[key];
    if (entry && Date.now() - entry.ts < CACHE_TTL) {
      return entry.data;
    }
    return null;
  }

  function setCachedAnime(title, data) {
    const cache = getCache();
    const key = title.toLowerCase().trim();
    cache[key] = { data, ts: Date.now() };
    // Keep cache size under 500 entries
    const keys = Object.keys(cache);
    if (keys.length > 500) {
      const sorted = keys.sort((a, b) => (cache[a].ts || 0) - (cache[b].ts || 0));
      for (let i = 0; i < 100; i++) delete cache[sorted[i]];
    }
    setCache(cache);
  }

  async function fetchAnimeFromAniList(title) {
    // Check cache first
    const cached = getCachedAnime(title);
    if (cached) return cached;

    // Try AniList first
    let data = await _fetchAnilist(title);
    if (data) {
      setCachedAnime(title, data);
      return data;
    }

    // Fallback to Jikan
    data = await _fetchJikan(title);
    if (data) {
      setCachedAnime(title, data);
      return data;
    }

    // Fallback to Kitsu
    data = await _fetchKitsu(title);
    if (data) {
      setCachedAnime(title, data);
      return data;
    }

    return null;
  }

  async function _fetchAnilist(title) {
    const query = `
      query ($search: String) {
        Media(search: $search, type: ANIME) {
          id
          title { romaji english native }
          description(asHtml: false)
          coverImage { large medium }
          bannerImage
          format
          status
          episodes
          duration
          genres
          tags { name }
          averageScore
          meanScore
          popularity
          favourites
          ranked
          nextAiringEpisode { episode airingAt }
          mediaMal { id }
          ExternalLinks { site url }
          streamingEpisodes { title thumbnail url site }
          relations { edges { relationType node { id title { romaji } type } } }
          characters(sort: ROLE, perPage: 10) { edges { role node { id name { full } image { medium } } voiceActors { id name { full } image { medium } } } }
          studios(isMain: true) { nodes { name } }
          producers { nodes { name } }
        }
      }
    `;

    try {
      const res = await fetch("https://graphql.anilist.co", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "SmartFox/1.0" },
        body: JSON.stringify({ query, variables: { search: title } }),
      });

      if (!res.ok) throw new Error("AniList API error: " + res.status);

      const json = await res.json();
      const m = json.data?.Media;
      if (!m) return null;

      return {
        id: m.id,
        title: m.title?.romaji || title,
        title_english: m.title?.english || "",
        title_japanese: m.title?.native || "",
        synopsis: (m.description || "").replace(/<[^>]*>/g, "").trim(),
        poster: m.coverImage?.large || m.coverImage?.medium || "",
        banner: m.bannerImage || "",
        type: m.format || "TV",
        status: m.status || "Unknown",
        episodes: m.episodes || null,
        duration: m.duration || null,
        genres: m.genres || [],
        themes: (m.tags || []).map(t => t.name).slice(0, 10),
        score: m.averageScore || m.meanScore || null,
        popularity: m.popularity || null,
        favorites: m.favourites || null,
        ranked: m.ranked || null,
        mal_id: m.mediaMal?.id || null,
        external_links: (m.ExternalLinks || []).map(l => ({ site: l.site, url: l.url })).filter(l => l.url),
        streaming: (m.streamingEpisodes || []).map(e => ({ title: e.title, url: e.url, thumbnail: e.thumbnail, site: e.site })),
        characters: (m.characters?.edges || []).map(e => ({
          name: e.node?.name?.full || "",
          image: e.node?.image?.medium || "",
          role: e.role || "",
          voice_actor: e.voiceActors?.[0]?.name?.full || "",
          va_image: e.voiceActors?.[0]?.image?.medium || "",
        })),
        studios: (m.studios?.nodes || []).map(n => n.name),
        producers: (m.producers?.nodes || []).map(n => n.name),
        relations: (m.relations?.edges || []).map(e => ({
          relation: e.relationType || "",
          name: e.node?.title?.romaji || "",
          type: e.node?.type || "",
        })),
        url: m.mediaMal?.id ? `https://myanimelist.net/anime/${m.mediaMal.id}` : `https://anilist.co/anime/${m.id}`,
        source: "AniList",
      };
    } catch (err) {
      logError("AniList fetch", err);
      return null;
    }
  }

  async function _fetchJikan(title) {
    try {
      const res = await fetch(`https://api.jikan.moe/v4/anime?q=${encodeURIComponent(title)}&limit=1&sfw=true`);
      if (!res.ok) throw new Error("Jikan API error: " + res.status);

      const json = await res.json();
      const anime = (json.data || [])[0];
      if (!anime) return null;

      const images = anime.images?.jpg || {};
      const poster = (images.large_image_url || images.image_url || "").replace("http://", "https://");

      return {
        id: anime.mal_id,
        title: anime.title || title,
        title_english: anime.title_english || "",
        title_japanese: anime.title_japanese || "",
        synopsis: (anime.synopsis || "").substring(0, 500),
        poster: poster,
        banner: poster,
        type: anime.type || "TV",
        status: anime.status || "Unknown",
        episodes: anime.episodes || null,
        duration: anime.duration || null,
        genres: (anime.genres || []).map(g => g.name),
        themes: (anime.themes || []).map(t => t.name),
        score: anime.score || null,
        popularity: anime.popularity || null,
        favorites: anime.favorites || null,
        mal_id: anime.mal_id,
        url: anime.url || `https://myanimelist.net/anime/${anime.mal_id}`,
        characters: [],
        studios: (anime.studios || []).map(s => s.name),
        producers: (anime.producers || []).map(p => p.name),
        external_links: [],
        streaming: [],
        relations: [],
        source: "Jikan",
      };
    } catch (err) {
      logError("Jikan fetch", err);
      return null;
    }
  }

  async function _fetchKitsu(title) {
    try {
      const res = await fetch(`https://kitsu.io/api/edge/anime?filter[text]=${encodeURIComponent(title)}&page[limit]=1`, {
        headers: { "Accept": "application/vnd.api+json" },
      });
      if (!res.ok) throw new Error("Kitsu API error: " + res.status);

      const json = await res.json();
      const anime = (json.data || [])[0];
      if (!anime) return null;

      const attrs = anime.attributes || {};
      const titles = attrs.titles || {};
      const poster = attrs.posterImage || {};
      const cover = attrs.coverImage || {};

      return {
        id: anime.id,
        title: attrs.canonicalTitle || title,
        title_english: titles.en || "",
        title_japanese: titles.ja_jp || "",
        synopsis: (attrs.synopsis || "").substring(0, 500),
        poster: poster.original || poster.large || "",
        banner: cover.original || cover.large || "",
        type: attrs.showType || "TV",
        status: attrs.status || "Unknown",
        episodes: attrs.episodeCount || null,
        duration: attrs.episodeLength || null,
        genres: [],
        themes: [],
        score: attrs.averageRating ? parseFloat(attrs.averageRating) : null,
        url: `https://kitsu.io/anime/${anime.id}`,
        characters: [],
        studios: [],
        producers: [],
        external_links: [],
        streaming: [],
        relations: [],
        source: "Kitsu",
      };
    } catch (err) {
      logError("Kitsu fetch", err);
      return null;
    }
  }

  async function enrichLibraryEntry(entry) {
    if (!entry.title) return entry;
    // If already has good data, skip
    if (entry.poster && entry.synopsis && entry.genres?.length) return entry;
    // Fetch from AniList
    const data = await fetchAnimeFromAniList(entry.title);
    if (!data) return entry;
    // Merge: keep user data, fill in missing fields
    return {
      ...data,
      ...entry,
      poster: entry.poster || data.poster,
      banner: entry.banner || data.banner,
      synopsis: entry.synopsis || data.synopsis,
      genres: entry.genres?.length ? entry.genres : data.genres,
      themes: entry.themes?.length ? entry.themes : data.themes,
      score: entry.score || data.score,
      episodes: entry.episodes || data.episodes,
      type: entry.type || data.type,
      url: entry.url || data.url,
      external_links: data.external_links,
      streaming: data.streaming,
      characters: data.characters,
      studios: data.studios,
      producers: data.producers,
      relations: data.relations,
    };
  }

  function getCacheStats() {
    const cache = getCache();
    const keys = Object.keys(cache);
    const total = keys.length;
    const valid = keys.filter(k => Date.now() - cache[k].ts < CACHE_TTL).length;
    const expired = total - valid;
    return { total, valid, expired };
  }

  function clearCache() {
    localStorage.removeItem(CACHE_KEY);
    showToast("Anime cache cleared");
  }

  // Cached elements
  const el = (id) => document.getElementById(id);
  const form = document.querySelector("[data-loading-form]");
  const loading = document.querySelector("[data-loading]");

  /* ── Utilities ── */
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(str == null ? "" : String(str)));
    return div.innerHTML;
  }

  /** Escape a string for safe embedding inside a single-quoted HTML attribute. */
  function escapeAttr(str) {
    return escapeHtml(str).replace(/'/g, "&#39;");
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
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toast.classList.remove("show"), 2500);
  }

  function logError(context, err) {
    // Surface failures to the console without spamming toasts for routine network blips
    if (err && err.name !== "AbortError") {
      console.warn("[Smart Fox] " + context + ":", err);
    }
  }

  /* ── Theme ── */
  function initTheme() {
    const stored = localStorage.getItem("theme") || "dark";
    document.documentElement.setAttribute("data-theme", stored);
    syncThemeIcons(stored);
  }
  function syncThemeIcons(theme) {
    const toggle = el("theme-toggle");
    if (!toggle) return;
    const sun = toggle.querySelector(".icon-sun");
    const moon = toggle.querySelector(".icon-moon");
    if (sun) sun.style.display = theme === "light" ? "" : "none";
    if (moon) moon.style.display = theme === "dark" ? "" : "none";
  }
  function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    syncThemeIcons(next);
  }

  /* ── Loading Overlay ── */
  function showLoading(message, subtitle) {
    if (!loading) return;
    const title = loading.querySelector(".loading-title");
    const sub = el("loading-subtitle");
    if (title) title.textContent = message || "Searching...";
    if (sub) sub.textContent = subtitle || "Searching anime databases for matches";
    loading.hidden = false;
    if (state.loadingTimeout) clearTimeout(state.loadingTimeout);
    state.loadingTimeout = setTimeout(hideLoading, 60000);
  }
  function hideLoading() {
    if (loading) loading.hidden = true;
    if (state.loadingTimeout) { clearTimeout(state.loadingTimeout); state.loadingTimeout = null; }
  }

  /* ── Views ── */
  let _currentView = "search";

  function showView(view, subPage) {
    const searchView = el("search-view");
    const libraryView = el("library-view");
    const navSearch = el("nav-search");
    const navLibrary = el("nav-library");
    const subPageContainer = el("sub-page-container");

    // Hide sub-page if switching main views
    if (subPageContainer) {
      subPageContainer.innerHTML = "";
      subPageContainer.style.display = "none";
    }

    if (view === "library") {
      if (searchView) {
        searchView.classList.add("view-exit");
        setTimeout(() => {
          searchView.style.display = "none";
          searchView.classList.remove("view-exit");
        }, 200);
      }
      if (libraryView) {
        libraryView.style.display = "";
        libraryView.classList.remove("view-enter");
        void libraryView.offsetWidth;
        libraryView.classList.add("view-enter");
      }
      if (navSearch) navSearch.classList.remove("active");
      if (navLibrary) navLibrary.classList.add("active");
      loadLibrary();
    } else {
      if (libraryView) {
        libraryView.classList.add("view-exit");
        setTimeout(() => {
          libraryView.style.display = "none";
          libraryView.classList.remove("view-exit");
        }, 200);
      }
      if (searchView) {
        searchView.style.display = "";
        searchView.classList.remove("view-enter");
        void searchView.offsetWidth;
        searchView.classList.add("view-enter");
      }
      if (navSearch) navSearch.classList.add("active");
      if (navLibrary) navLibrary.classList.remove("active");
    }
    _currentView = view;
  }

  function showSubPage(status) {
    const libraryView = el("library-view");
    const subPageContainer = el("sub-page-container");
    if (!libraryView || !subPageContainer) return;

    // Hide main library content
    const grid = el("library-grid");
    const controls = libraryView.querySelector(".library-controls");
    if (grid) grid.style.display = "none";
    if (controls) controls.style.display = "none";

    // Show sub-page container
    subPageContainer.style.display = "";
    subPageContainer.classList.remove("view-enter");
    void subPageContainer.offsetWidth;
    subPageContainer.classList.add("view-enter");

    // Load entries for this status
    loadSubPage(status);
  }

  function hideSubPage() {
    const libraryView = el("library-view");
    const subPageContainer = el("sub-page-container");
    if (!libraryView || !subPageContainer) return;

    subPageContainer.classList.add("view-exit");
    setTimeout(() => {
      subPageContainer.innerHTML = "";
      subPageContainer.style.display = "none";
      subPageContainer.classList.remove("view-exit");

      // Show main library content
      const grid = el("library-grid");
      const controls = libraryView.querySelector(".library-controls");
      if (grid) grid.style.display = "";
      if (controls) controls.style.display = "";
    }, 200);
  }

  function loadSubPage(status) {
    const subPageContainer = el("sub-page-container");
    if (!subPageContainer) return;

    const statusLabels = {
      watching: "Watching",
      completed: "Completed",
      plan_to_watch: "Plan to Watch",
      on_hold: "On Hold",
      dropped: "Dropped"
    };

    const statusIcons = {
      watching: "play_circle",
      completed: "check_circle",
      plan_to_watch: "schedule",
      on_hold: "pause_circle",
      dropped: "cancel"
    };

    const label = statusLabels[status] || status;
    const icon = statusIcons[status] || "collections_bookmark";

    subPageContainer.innerHTML = `
      <div class="sub-page">
        <div class="sub-page-header">
          <button class="sub-page-back" data-action="close-sub-page" aria-label="Back to library">
            <span class="material-symbols-rounded">arrow_back</span>
          </button>
          <span class="material-symbols-rounded" style="font-size:28px; color:var(--accent);">${icon}</span>
          <h2 class="sub-page-title">${label}</h2>
          <span class="sub-page-count" id="sub-page-count">Loading...</span>
        </div>
        <div class="sub-page-grid" id="sub-page-grid">
          <div class="muted center" style="grid-column:1/-1; padding:40px;">Loading...</div>
        </div>
      </div>`;

    // Fetch entries for this status
    const q = el("library-search")?.value || "";
    const params = { status };
    if (q) params.q = q;
    params.sort = _librarySort;
    params.desc = _librarySortDesc ? "1" : "0";
    const qs = new URLSearchParams(params).toString();

    fetch("/api/library?" + qs).then(r => r.json()).then(data => {
      const entries = data.entries || [];
      const countEl = el("sub-page-count");
      if (countEl) countEl.textContent = `${entries.length} ${entries.length === 1 ? "anime" : "anime"}`;

      const gridEl = el("sub-page-grid");
      if (!gridEl) return;

      if (!entries.length) {
        gridEl.innerHTML = `
          <div class="sub-page-empty">
            <span class="material-symbols-rounded">${icon}</span>
            <p>No anime in "${label}"</p>
            <span class="muted">Anime with this status will appear here</span>
          </div>`;
        return;
      }

      gridEl.innerHTML = entries.map((e, i) => {
        const ratingHtml = e.rating ? `<span class="library-card-rating"><span class="material-symbols-rounded">star</span>${e.rating}</span>` : "";
        const progressHtml = e.total_episodes ? `<span class="library-card-progress">${e.progress || 0}/${e.total_episodes}</span>` : "";
        const posterHtml = e.poster
          ? `<img src="${escapeAttr(e.poster)}" alt="${escapeAttr(e.title)}" loading="lazy" class="lib-poster" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
             <div class="no-poster" style="display:none;"><span class="material-symbols-rounded">image</span></div>`
          : `<div class="no-poster"><span class="material-symbols-rounded">image</span></div>`;
        const stagger = ` style="animation-delay:${Math.min(i * 30, 480)}ms"`;

        return `
          <div class="library-card animate-stagger" data-action="open-library-edit" data-id="${escapeAttr(e.id)}" data-title="${escapeAttr(e.title)}"${stagger}>
            <div class="library-card-poster">${posterHtml}</div>
            <div class="library-card-body">
              <div class="library-card-title" title="${escapeAttr(e.title)}">${escapeHtml(e.title)}</div>
              <div class="library-card-meta">
                <span class="library-card-status status-${e.status}">${label}</span>
                ${ratingHtml}
              </div>
              ${progressHtml}
            </div>
          </div>`;
      }).join("");
    }).catch(err => {
      logError("loadSubPage", err);
      const gridEl = el("sub-page-grid");
      if (gridEl) gridEl.innerHTML = `<div class="muted center" style="grid-column:1/-1;">Failed to load</div>`;
    });
  }

  /* ── Scroll Helpers ── */
  function scrollToResults() {
    const panel = el("ai-status-panel");
    const recs = el("ai-recommendations");
    const target = (recs && recs.offsetParent !== null) ? recs : panel;
    if (target) {
      setTimeout(() => target.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
    }
  }

  /* ── Recommendation Cards ── */
  function buildRecCard(item, index) {
    const genres = (item.genres || []).slice(0, 4).map(g => `<span>${escapeHtml(g)}</span>`).join("");
    const url = item.url ? `<a class="rec-link" href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">MAL ↗</a>` : "";
    const matchReason = item.match_reason ? `<div class="rec-match-reason">${escapeHtml(item.match_reason)}</div>` : "";
    const posterSrc = item.poster || "";
    const simPct = Math.min(100, Math.max(0, item.similarity_percentage || 0));
    const scoreLabel = item.score ? `★ ${item.score}` : (item.rating ? `${item.rating}%` : "~");

    // data-json uses escapeAttr to neutralize single quotes (the attribute delimiter)
    const dataJson = escapeAttr(JSON.stringify({
      title: item.title || "",
      poster: posterSrc,
      score: item.score,
      episodes: item.episodes,
      type: item.type,
      genres: item.genres || [],
      url: item.url || "",
      synopsis: item.synopsis || item.overall_explanation || "",
    }));

    const stagger = (typeof index === "number") ? ` style="animation-delay:${Math.min(index * 40, 600)}ms"` : "";

    const posterHtml = posterSrc
      ? `<img class="rec-poster" alt="${escapeAttr(item.title || "")}"
           loading="lazy"
           onload="this.parentElement.classList.add('loaded')"
           onerror="this.classList.add('no-src'); this.parentElement.classList.add('loaded')"
           src="${escapeAttr(posterSrc)}">`
      : `<img class="rec-poster no-src" alt="">`;

    return `
      <div class="rec-card animate-stagger" data-title="${escapeAttr(item.title || "")}" data-rank="${item.rank || 0}" data-poster="${escapeAttr(posterSrc)}" data-json="${dataJson}" data-action="show-detail-card"${stagger}>
        <div class="rec-poster-wrap">
          ${posterHtml}
          <div class="rec-poster-placeholder">${item.rank || "?"}</div>
          <div class="rec-rating-badge">${scoreLabel}</div>
        </div>
        <div class="rec-body">
          <div class="rec-head">
            <strong title="${escapeAttr(item.title || "Unknown")}">${escapeHtml(item.title || "Unknown")}</strong>
            <em>${simPct.toFixed(0)}%</em>
          </div>
          ${matchReason}
          <p class="rec-synopsis">${escapeHtml(item.synopsis || item.overall_explanation || "")}</p>
          ${genres ? `<div class="rec-genres">${genres}</div>` : ""}
          <div class="rec-sim-bar"><span style="width:${simPct}%"></span></div>
          <div class="rec-actions">
            ${url}
            <button class="rec-save-btn" data-action="save-from-card" title="Add to Library">
              <span class="material-symbols-rounded">add</span> Save
            </button>
          </div>
        </div>
      </div>`;
  }

  /* ── Search Flow ── */
  function clearAll() {
    state.pollGen++;
    if (state.currentEventSource) { state.currentEventSource.close(); state.currentEventSource = null; }
    state.currentTaskId = null;
    const main = document.querySelector(".container");
    if (main) {
      main.querySelectorAll("section, .profile-layout, article, .dialog-scrim").forEach(node => {
        if (!node.closest("#search-view") && !node.closest(".dialog-scrim")) node.remove();
      });
    }
  }

  function setSearchInProgress(inProgress) {
    state.searchInProgress = inProgress;
    const submitBtn = el("search-submit");
    if (submitBtn) submitBtn.disabled = inProgress;
  }

  function cancelSearch() {
    if (!state.currentTaskId) return;
    fetch("/api/ai/cancel/" + state.currentTaskId, { method: "POST" })
      .then(r => r.json()).then(data => {
        if (data.status === "cancelled") {
          showToast("Search cancelled");
          if (state.currentEventSource) { state.currentEventSource.close(); state.currentEventSource = null; }
          updateStatus(100, "Cancelled", "");
          hideCancelBtn();
          hideLoading();
          setSearchInProgress(false);
        }
      }).catch(err => logError("cancelSearch", err));
  }

  function showCancelBtn() { const btn = el("cancel-search-btn"); if (btn) btn.style.display = ""; }
  function hideCancelBtn() { const btn = el("cancel-search-btn"); if (btn) btn.style.display = "none"; }

  function buildResultSection() {
    const main = document.querySelector(".container");
    if (!main || el("ai-status-panel")) return;
    main.insertAdjacentHTML("beforeend", `
      <section class="profile-layout animate-fade-in" style="margin-top: 24px;">
        <div class="profile-grid">
          <div class="profile-main" style="grid-column: 1 / -1;">
            <div class="card card-filled" id="ai-status-panel">
              <h3 class="card-title">AI Search Status</h3>
              <div class="ai-status-box">
                <div class="status-header">
                  <div class="status-text" id="ai-status-text">Initializing...</div>
                  <div class="status-count" id="ai-status-count">0 found</div>
                </div>
                <div class="progress-track"><div class="progress-bar" id="ai-status-bar" style="width: 0%"></div></div>
                <div class="status-detail muted" id="ai-status-detail"></div>
                <button class="btn btn-text" id="cancel-search-btn" type="button" style="display:none;">Cancel</button>
              </div>
            </div>
            <div class="card card-elevated" id="ai-recommendations">
              <div class="rec-header">
                <h3 class="card-title" id="ai-rec-title">AI Recommendations <span id="rec-total-count" class="muted"></span></h3>
                <div class="rec-header-actions">
                  <button class="icon-btn" data-action="share-recs" title="Share"><span class="material-symbols-rounded">share</span></button>
                </div>
              </div>
              <p class="rec-hint muted">Click any card to see full details</p>
              <div class="rec-grid" id="rec-grid"></div>
            </div>
          </div>
        </div>
      </section>`);
  }

  function startSearch(query, description, contentFilter, negativePrompt) {
    clearAll();
    buildResultSection();
    const recGrid = el("rec-grid");
    if (recGrid) recGrid.innerHTML = "";
    updateStatus(0, "Searching...", "");
    showCancelBtn();
    setSearchInProgress(true);
    showLoading("Searching anime databases...", "Querying AniList, Jikan & Kitsu in parallel");

    const aiEnabled = el("ai-toggle")?.checked !== false;

    if (!aiEnabled) {
      // Simple search without AI - just fetch from AniList
      fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, content_filter: contentFilter }),
      }).then(r => r.json()).then(data => {
        hideLoading();
        setSearchInProgress(false);
        hideCancelBtn();
        if (data.error) {
          updateStatus(100, "Error: " + data.error, "");
          return;
        }
        if (data.profile) {
          // Show profile results
          updateStatus(100, "Search complete", "1 found");
          showProfileResults(data.profile);
        } else {
          updateStatus(100, "No results found", "");
        }
      }).catch(err => {
        hideLoading();
        updateStatus(100, "Failed: " + err.message, "");
        setSearchInProgress(false);
        hideCancelBtn();
        showToast("Search failed: " + err.message);
      });
      return;
    }

    // AI-powered search
    fetch("/api/ai/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, description, content_filter: contentFilter, negative_prompt: negativePrompt }),
    }).then(r => r.json()).then(data => {
      if (data.error) {
        hideLoading();
        updateStatus(100, "Error: " + data.error, "");
        setSearchInProgress(false);
        hideCancelBtn();
        return;
      }
      state.currentTaskId = data.task_id;
      hideLoading();
      listenToStream(state.currentTaskId);
    }).catch(err => {
      hideLoading();
      updateStatus(100, "Failed: " + err.message, "");
      setSearchInProgress(false);
      hideCancelBtn();
      showToast("Search failed: " + err.message);
    });
  }

  function showProfileResults(profile) {
    const main = document.querySelector(".container");
    if (!main) return;
    // Build profile HTML similar to server-rendered template
    const title = profile.titles?.all?.[0] || profile.titles?.english?.[0] || "Unknown";
    const poster = profile.media?.poster || "";
    const banner = profile.media?.banner || "";
    const synopsis = profile.description?.summary || "No description found.";
    const genres = (profile.genres || []).slice(0, 12).map(g => `<span class="chip chip-accent">${escapeHtml(g)}</span>`).join("");
    const themes = (profile.themes || []).slice(0, 12).map(t => `<span class="chip chip-soft">${escapeHtml(t)}</span>`).join("");

    const posterHtml = poster
      ? `<img class="poster-img" src="${escapeAttr(poster)}" alt="${escapeAttr(title)} poster">`
      : `<div class="poster-placeholder"><span class="material-symbols-rounded">image_not_supported</span></div>`;

    const bannerHtml = banner
      ? `<div class="hero-banner"><img src="${escapeAttr(banner)}" alt="Banner" class="banner-img"><div class="banner-scrim"></div></div>`
      : "";

    const profileJson = escapeAttr(JSON.stringify(profile));

    const html = `
      ${bannerHtml}
      <section class="profile-layout animate-fade-in">
        <div class="profile-grid">
          <aside class="profile-sidebar">
            <div class="poster-wrap elevation-3">${posterHtml}</div>
            <button class="btn btn-outlined full-width" data-action="add-from-profile-data" data-profile="${profileJson}">
              <span class="material-symbols-rounded">add</span> Add to Library
            </button>
          </aside>
          <div class="profile-main">
            <h1 class="title-display">${escapeHtml(title)}</h1>
            <p class="synopsis">${escapeHtml(synopsis)}</p>
            <div class="chips-wrap">${genres}${themes}</div>
            <div class="card card-filled" id="ai-recommendations" style="display:none;">
              <div class="rec-header">
                <h3 class="card-title">AI Recommendations</h3>
              </div>
              <div class="rec-grid" id="rec-grid"></div>
            </div>
          </div>
        </div>
      </section>`;

    main.insertAdjacentHTML("beforeend", html);
    scrollToResults();
  }

  function listenToStream(taskId) {
    if (state.currentEventSource) state.currentEventSource.close();
    const es = new EventSource("/api/ai/stream/" + taskId);
    state.currentEventSource = es;

    es.onmessage = function (event) {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        logError("SSE parse", e);
        return;
      }
      updateStatus(data.progress || 0, data.message || "", data.count > 0 ? `${data.count} found` : "");
      if (data.commentary && Array.isArray(data.commentary)) updateCommentary(data.commentary);
      if (data.latest) appendRecommendation(data.latest);

      if (data.status === "done" || data.status === "error" || data.status === "cancelled") {
        es.close(); state.currentEventSource = null; hideCancelBtn();
        setSearchInProgress(false);
        if (data.status === "done") {
          if (data.recommendation?.top_50?.length) {
            const recGrid = el("rec-grid");
            if (recGrid) {
              recGrid.dataset.sourceTitle = data.recommendation.source_title || "";
              recGrid.innerHTML = data.recommendation.top_50.map((item, i) => buildRecCard(item, i)).join("");
              markAlreadySaved();
              recGrid.querySelectorAll(".rec-poster:not(.no-src)").forEach(img => {
                if (img.complete && img.naturalWidth) img.closest(".rec-poster-wrap")?.classList.add("loaded");
              });
              fetchPostersForGrid();
              updateRecCount();
            }
          }
          hideLoading();
          scrollToResults();
        } else if (data.status === "error") {
          hideLoading();
          showToast("Search error: " + (data.message || "unknown"));
        } else {
          hideLoading();
        }
      }
    };

    es.onerror = function () {
      es.close(); state.currentEventSource = null;
      setTimeout(() => pollTaskStatus(taskId, state.pollGen), 1000);
    };
  }

  function updateCommentary(lines) {
    const node = el("ai-status-detail");
    if (!node) return;
    node.textContent = lines.slice(-8).map(l => l.replace(/^##\s*/, "")).join("\n");
  }

  function pollTaskStatus(taskId, gen) {
    if (gen !== undefined && gen !== state.pollGen) return;
    fetch("/api/ai/status/" + taskId).then(r => r.json()).then(data => {
      updateStatus(data.progress || 0, data.message || "", (data.results || []).length > 0 ? `${data.results.length} found` : "");
      if (data.status === "done" || data.status === "error") {
        hideLoading();
        setSearchInProgress(false);
        if (data.status === "done") { loadFullResults(taskId); scrollToResults(); }
        return;
      }
      setTimeout(() => pollTaskStatus(taskId, gen), 500);
    }).catch(err => {
      logError("pollTaskStatus", err);
      setTimeout(() => pollTaskStatus(taskId, gen), 1000);
    });
  }

  function loadFullResults(taskId) {
    hideLoading();
    fetch("/api/ai/status/" + taskId).then(r => r.json()).then(data => {
      const recGrid = el("rec-grid");
      if (!recGrid) return;
      let items = [];
      if (data.recommendation) {
        items = data.recommendation.top_50 || [];
        recGrid.dataset.sourceTitle = data.recommendation.source_title || "";
      } else if (data.results?.length) { items = data.results; }
      if (items.length > 0) {
        recGrid.innerHTML = items.map((item, i) => buildRecCard(item, i)).join("");
        markAlreadySaved();
        recGrid.querySelectorAll(".rec-poster:not(.no-src)").forEach(img => {
          if (img.complete && img.naturalWidth) img.closest(".rec-poster-wrap")?.classList.add("loaded");
        });
      } else if (!recGrid.children.length) {
        recGrid.innerHTML = renderEmptyState("search_off", "No recommendations found", "Try a different title or description.");
      }
      fetchPostersForGrid();
      updateRecCount();
    }).catch(err => logError("loadFullResults", err));
  }

  function markAlreadySaved() {
    fetch("/api/library").then(r => r.json()).then(data => {
      const titles = new Set((data.entries || []).map(e => e.title.toLowerCase()));
      document.querySelectorAll(".rec-card").forEach(card => {
        if (titles.has((card.dataset.title || "").toLowerCase())) {
          const btn = card.querySelector(".rec-save-btn");
          if (btn) { btn.classList.add("saved"); btn.innerHTML = '<span class="material-symbols-rounded">check</span> Saved'; }
        }
      });
    }).catch(err => logError("markAlreadySaved", err));
  }

  function updateStatus(progress, message, countText) {
    const bar = el("ai-status-bar");
    const text = el("ai-status-text");
    const detail = el("ai-status-detail");
    const count = el("ai-status-count");
    if (bar) bar.style.width = progress + "%";
    if (text) text.textContent = message;
    if (detail && !detail.textContent) detail.textContent = `Progress: ${progress}%`;
    if (count) count.textContent = countText;
  }

  function appendRecommendation(item) {
    const grid = el("rec-grid");
    if (!grid) return;
    const idx = grid.querySelectorAll(".rec-card").length;
    grid.insertAdjacentHTML("beforeend", buildRecCard(item, idx));
    const lastCard = grid.lastElementChild;
    if (lastCard) fetchPosterForCard(lastCard);
    updateRecCount();
  }

  function updateRecCount() {
    const grid = el("rec-grid");
    const count = el("rec-total-count");
    if (grid && count) {
      const total = grid.querySelectorAll(".rec-card").length;
      count.textContent = `${total} recommendation${total !== 1 ? "s" : ""}`;
    }
  }

  function fetchPostersForGrid() {
    const cards = document.querySelectorAll("#rec-grid .rec-card[data-title]");
    if (!cards.length) return;
    const needPoster = Array.from(cards).filter(c => { const img = c.querySelector(".rec-poster"); return !img || img.classList.contains("no-src"); });
    if (!needPoster.length) return;
    const titles = needPoster.map(c => c.dataset.title).filter(Boolean);
    if (!titles.length) return;
    const contentFilter = el("content_filter")?.value || "sfw";
    fetch("/api/recommend/posters", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ titles, content_filter: contentFilter }),
    }).then(r => r.ok ? r.json() : {}).then(data => {
      needPoster.forEach(card => applyPosterData(card, data[card.dataset.title]));
    }).catch(err => logError("fetchPostersForGrid", err));
  }

  function fetchPosterForCard(card) {
    const title = card.dataset.title;
    if (!title) return;
    const contentFilter = el("content_filter")?.value || "sfw";
    fetch("/api/recommend/posters", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ titles: [title], content_filter: contentFilter }),
    }).then(r => r.ok ? r.json() : {}).then(data => applyPosterData(card, data[title]))
      .catch(err => logError("fetchPosterForCard", err));
  }

  function applyPosterData(card, info) {
    if (!info || !info.poster) return;
    const posterWrap = card.querySelector(".rec-poster-wrap");
    let posterImg = card.querySelector(".rec-poster");
    if (!posterImg || posterImg.classList.contains("no-src")) {
      if (posterImg) posterImg.remove();
      posterImg = document.createElement("img");
      posterImg.className = "rec-poster";
      posterImg.alt = (card.dataset.title || "") + " poster";
      if (posterWrap) posterWrap.prepend(posterImg);
    }
    posterImg.onload = () => { if (posterWrap) posterWrap.classList.add("loaded"); };
    posterImg.onerror = () => { posterImg.classList.add("no-src"); if (posterWrap) posterWrap.classList.add("loaded"); };
    posterImg.src = info.poster;
    const badge = card.querySelector(".rec-rating-badge");
    if (badge && info.score) badge.textContent = `★ ${info.score}`;
    const syn = card.querySelector(".rec-synopsis");
    if (syn && info.synopsis && !syn.textContent.trim()) syn.textContent = info.synopsis;
    const genres = card.querySelector(".rec-genres");
    if (genres && info.genres?.length && !genres.children.length) {
      info.genres.slice(0, 4).forEach(g => { const s = document.createElement("span"); s.textContent = g; genres.appendChild(s); });
    }
  }

  /* ── Detail Modal ── */
  function showDetail(title) {
    const modal = el("detail-modal");
    const loadingEl = el("detail-loading");
    const content = el("detail-content");
    if (!modal) return;
    openDialog(modal);
    if (loadingEl) loadingEl.style.display = "flex";
    if (content) content.style.display = "none";

    const contentFilter = el("content_filter")?.value || "sfw";
    fetch("/api/anime/detail?title=" + encodeURIComponent(title) + "&content_filter=" + encodeURIComponent(contentFilter))
      .then(r => r.json()).then(data => {
        if (loadingEl) loadingEl.style.display = "none";
        if (content) content.style.display = "block";
        if (data.error) { content.innerHTML = `<div class="alert alert-error">${escapeHtml(data.error)}</div>`; return; }
        content.innerHTML = buildDetailHTML(data);
      }).catch(err => {
        if (loadingEl) loadingEl.style.display = "none";
        if (content) content.style.display = "block";
        content.innerHTML = `<div class="alert alert-error">Failed to load: ${escapeHtml(err.message)}</div>`;
      });
  }

  function closeDetail() { closeDialog(el("detail-modal")); }

  function buildDetailHTML(d) {
    const genres = (d.genres || []).map(g => `<span class="chip chip-accent">${escapeHtml(g)}</span>`).join("");
    const themes = (d.themes || []).map(t => `<span class="chip chip-soft">${escapeHtml(t)}</span>`).join("");
    const demographics = (d.demographics || []).map(dm => `<span class="chip chip-soft">${escapeHtml(dm)}</span>`).join("");
    const studios = (d.studios || []).join(", ") || "Unknown";
    const producers = (d.producers || []).join(", ") || "Unknown";
    const detailJson = escapeAttr(JSON.stringify({ title: d.title || "", poster: d.poster || "", score: d.score, episodes: d.episodes, type: d.type || "TV", genres: d.genres || [], url: d.url || "" }));

    const posterHtml = d.poster
      ? `<div class="detail-poster-wrap">
           <img class="detail-poster" src="${escapeAttr(d.poster)}" alt="${escapeAttr(d.title || "")}"
             loading="lazy"
             onload="this.parentElement.classList.add('loaded')"
             onerror="this.classList.add('no-src'); this.parentElement.classList.add('loaded')">
           <div class="detail-poster-placeholder">
             <span class="material-symbols-rounded">image</span>
           </div>
         </div>`
      : `<div class="detail-poster-wrap loaded">
           <div class="detail-poster-placeholder">
             <span class="material-symbols-rounded">image</span>
           </div>
         </div>`;

    const relationsHtml = (d.relations && d.relations.length > 0)
      ? `<div class="card card-filled details-card">
           <h3 class="card-title"><span class="material-symbols-rounded">hub</span> Relations</h3>
           <div class="relation-grid">${d.relations.map(r => `
             <div class="relation-group">
               <span class="relation-label">${escapeHtml(r.relation)}</span>
               ${r.entries.map(e => `
                 <a class="relation-item" href="${escapeAttr(e.url || '#')}" target="_blank" rel="noreferrer">
                   <span class="relation-type">${escapeHtml(e.type || '')}</span>
                   <span class="relation-name">${escapeHtml(e.name || '')}</span>
                 </a>
               `).join('')}
             </div>
           `).join('')}</div>
         </div>`
      : "";

    const ops = d.opening_themes || [];
    const eds = d.ending_themes || [];
    const themesHtml = (ops.length > 0 || eds.length > 0)
      ? `<div class="card card-filled details-card">
           <h3 class="card-title"><span class="material-symbols-rounded">music_note</span> Themes</h3>
           ${ops.length > 0 ? `<div class="theme-section"><span class="theme-label">Openings</span>${ops.map(t => `<span class="theme-item">${escapeHtml(t)}</span>`).join('')}</div>` : ""}
           ${eds.length > 0 ? `<div class="theme-section" style="margin-top:10px;"><span class="theme-label">Endings</span>${eds.map(t => `<span class="theme-item">${escapeHtml(t)}</span>`).join('')}</div>` : ""}
         </div>`
      : "";

    const externals = d.external_links || [];
    const streaming = d.streaming || [];
    const linksHtml = (externals.length > 0 || streaming.length > 0)
      ? `<div class="card card-filled details-card">
           <h3 class="card-title"><span class="material-symbols-rounded">link</span> Links</h3>
           <div class="link-grid">
             ${streaming.map(s => `<a class="link-item streaming" href="${escapeAttr(s.url)}" target="_blank" rel="noreferrer"><span class="material-symbols-rounded">play_circle</span> ${escapeHtml(s.name)}</a>`).join('')}
             ${externals.map(e => `<a class="link-item" href="${escapeAttr(e.url)}" target="_blank" rel="noreferrer"><span class="material-symbols-rounded">open_in_new</span> ${escapeHtml(e.name)}</a>`).join('')}
           </div>
         </div>`
      : "";

    const trailerHtml = d.trailer_embed
      ? `<div class="card card-filled details-card">
           <h3 class="card-title"><span class="material-symbols-rounded">play_arrow</span> Trailer</h3>
           <div class="trailer-embed-wrap">
             <iframe class="trailer-embed" src="${escapeAttr(d.trailer_embed)}" allowfullscreen loading="lazy"></iframe>
           </div>
         </div>`
      : "";

    const synonyms = d.title_synonyms && d.title_synonyms.length > 0
      ? `<p class="muted" style="font-size:0.82rem;">Also known as: ${d.title_synonyms.map(s => escapeHtml(s)).join(", ")}</p>`
      : "";

    return `
      <div class="detail-hero">
        ${posterHtml}
        <div class="detail-hero-info">
          <h2 class="title-display" style="font-size:1.5rem;">${escapeHtml(d.title || "Unknown")}</h2>
          ${d.title_english && d.title_english !== d.title ? `<p class="muted" style="font-size:1rem;">${escapeHtml(d.title_english)}</p>` : ""}
          ${d.title_japanese ? `<p class="muted" style="font-size:0.85rem;">${escapeHtml(d.title_japanese)}</p>` : ""}
          ${synonyms}
          <div class="chips-wrap" style="margin-top:8px;">${genres}${themes}${demographics}</div>
          <div class="detail-hero-actions">
            <button class="btn btn-filled" data-detail-json="${detailJson}" data-action="add-from-detail">
              <span class="material-symbols-rounded">add</span> Add to Library
            </button>
          </div>
        </div>
      </div>
      ${d.synopsis ? `<div class="card card-filled details-card"><h3 class="card-title"><span class="material-symbols-rounded">article</span> Synopsis</h3><p class="synopsis">${escapeHtml(d.synopsis)}</p></div>` : ""}
      ${d.background ? `<div class="card card-filled details-card"><h3 class="card-title"><span class="material-symbols-rounded">info</span> Background</h3><p class="synopsis">${escapeHtml(d.background)}</p></div>` : ""}
      ${relationsHtml}
      ${themesHtml}
      ${trailerHtml}
      <div class="card card-filled details-card">
        <h3 class="card-title"><span class="material-symbols-rounded">bar_chart</span> Information</h3>
        <div class="info-grid">
          <div class="info-item"><span class="info-label">Score</span><span class="info-value"><span class="material-symbols-rounded star-icon">star</span>${d.score || "N/A"}</span></div>
          <div class="info-item"><span class="info-label">Ranked</span><span class="info-value">#${d.rank || "N/A"}</span></div>
          <div class="info-item"><span class="info-label">Popularity</span><span class="info-value">#${d.popularity || "N/A"}</span></div>
          <div class="info-item"><span class="info-label">Episodes</span><span class="info-value">${d.episodes || "Unknown"}</span></div>
          <div class="info-item"><span class="info-label">Status</span><span class="info-value">${escapeHtml(d.status || "Unknown")}</span></div>
          <div class="info-item"><span class="info-label">Type</span><span class="info-value">${escapeHtml(d.type || "Unknown")}</span></div>
          <div class="info-item"><span class="info-label">Source</span><span class="info-value">${escapeHtml(d.source || "Unknown")}</span></div>
          <div class="info-item"><span class="info-label">Duration</span><span class="info-value">${escapeHtml(d.duration || "Unknown")}</span></div>
          <div class="info-item"><span class="info-label">Rating</span><span class="info-value">${escapeHtml(d.rating_val || d.rating || "Unknown")}</span></div>
          <div class="info-item"><span class="info-label">Scored By</span><span class="info-value">${d.scored_by ? d.scored_by.toLocaleString() : "N/A"}</span></div>
          <div class="info-item"><span class="info-label">Members</span><span class="info-value">${d.members ? d.members.toLocaleString() : "N/A"}</span></div>
          <div class="info-item"><span class="info-label">Favorites</span><span class="info-value">${d.favorites ? d.favorites.toLocaleString() : "N/A"}</span></div>
        </div>
      </div>
      <div class="card card-filled details-card">
        <h3 class="card-title"><span class="material-symbols-rounded">theater_comedy</span> Production</h3>
        <div class="info-grid">
          <div class="info-item"><span class="info-label">Studios</span><span class="info-value">${escapeHtml(studios)}</span></div>
          <div class="info-item"><span class="info-label">Producers</span><span class="info-value">${escapeHtml(producers)}</span></div>
          ${d.aired ? `<div class="info-item full-width"><span class="info-label">Aired</span><span class="info-value">${escapeHtml(d.aired)}</span></div>` : ""}
        </div>
      </div>
      ${linksHtml}
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;">
        ${d.url ? `<a class="btn btn-filled" href="${escapeAttr(d.url)}" target="_blank" rel="noreferrer">View on MyAnimeList</a>` : ""}
        ${d.trailer ? `<a class="btn btn-outlined" href="${escapeAttr(d.trailer)}" target="_blank" rel="noreferrer"><span class="material-symbols-rounded">play_arrow</span> Watch Trailer</a>` : ""}
      </div>`;
  }

  /* ── Share ── */
  function shareRecommendations() {
    const grid = el("rec-grid");
    if (!grid) return;
    const src = grid.dataset.sourceTitle || "Anime";
    const cards = grid.querySelectorAll(".rec-card");
    if (!cards.length) { showToast("No recommendations to share yet"); return; }
    const lines = [`Recommendations for ${src}:\n`];
    cards.forEach(c => { const em = c.querySelector(".rec-head em"); lines.push(`#${c.dataset.rank} ${c.dataset.title} (${em ? em.textContent : ""})`); });
    const text = lines.join("\n");
    if (navigator.share) navigator.share({ title: `Recommendations for ${src}`, text });
    else if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard!"));
    else showToast("Sharing not supported");
  }

  function showDetailFromCard(cardEl) {
    const title = cardEl.dataset.title || "";
    if (title) showDetail(title);
    else showToast("No title available for detail");
  }

  function saveFromCard(btnEl) {
    const card = btnEl.closest(".rec-card");
    if (!card) return;
    const json = card.dataset.json;
    if (!json) return;
    try {
      const data = JSON.parse(json);
      addToLibrary(data);
      btnEl.classList.add("saved");
      btnEl.innerHTML = '<span class="material-symbols-rounded">check</span> Saved';
    } catch (e) {
      logError("saveFromCard parse", e);
      showToast("Could not save: invalid data");
    }
  }

  /* ── Library ── */
  function loadLibrary() {
    const q = el("library-search")?.value || "";
    const params = {};
    if (q) params.q = q;
    if (_libraryFilter) params.status = _libraryFilter;
    params.sort = _librarySort;
    params.desc = _librarySortDesc ? "1" : "0";
    const qs = new URLSearchParams(params).toString();
    const url = "/api/library" + (qs ? "?" + qs : "");
    fetch(url).then(r => r.json()).then(data => {
      _libraryEntries = data.entries || [];
      const stats = data.stats || {};
      renderLibraryStats(stats);
      renderLibraryGrid(_libraryEntries);
      updateLibraryBadge(stats.total || 0);
    }).catch(err => {
      logError("loadLibrary", err);
      showToast("Failed to load library");
    });
  }

  function renderLibraryStats(stats) {
    const node = el("library-stats");
    if (!node) return;
    node.innerHTML = `
      <div class="library-stat"><span class="library-stat-label">Total</span><strong class="library-stat-value">${stats.total || 0}</strong></div>
      <button class="library-stat library-stat-btn" data-action="open-sub-page" data-status="watching" type="button">
        <span class="library-stat-label">Watching</span>
        <strong class="library-stat-value accent">${stats.watching || 0}</strong>
      </button>
      <button class="library-stat library-stat-btn" data-action="open-sub-page" data-status="completed" type="button">
        <span class="library-stat-label">Completed</span>
        <strong class="library-stat-value">${stats.completed || 0}</strong>
      </button>
      <button class="library-stat library-stat-btn" data-action="open-sub-page" data-status="plan_to_watch" type="button">
        <span class="library-stat-label">Plan to Watch</span>
        <strong class="library-stat-value">${stats.plan_to_watch || 0}</strong>
      </button>
      <button class="library-stat library-stat-btn" data-action="open-sub-page" data-status="on_hold" type="button">
        <span class="library-stat-label">On Hold</span>
        <strong class="library-stat-value">${stats.on_hold || 0}</strong>
      </button>
      <button class="library-stat library-stat-btn" data-action="open-sub-page" data-status="dropped" type="button">
        <span class="library-stat-label">Dropped</span>
        <strong class="library-stat-value">${stats.dropped || 0}</strong>
      </button>
    `;
  }

  function renderLibraryGrid(entries) {
    const grid = el("library-grid");
    const empty = el("library-empty");
    if (!grid) return;

    if (!entries.length) {
      grid.innerHTML = "";
      if (empty) { grid.appendChild(empty); empty.style.display = ""; }
      return;
    }
    if (empty) empty.style.display = "none";

    grid.innerHTML = entries.map((e, i) => {
      const statusClass = `status-${e.status}`;
      const statusLabel = (e.status || "plan_to_watch").replace(/_/g, " ");
      const ratingHtml = e.rating ? `<span class="library-card-rating"><span class="material-symbols-rounded">star</span>${e.rating}</span>` : "";
      const progressHtml = e.total_episodes ? `<span class="library-card-progress">${e.progress || 0}/${e.total_episodes}</span>` : "";
      const posterHtml = e.poster
        ? `<img src="${escapeAttr(e.poster)}" alt="${escapeAttr(e.title)}" loading="lazy" class="lib-poster" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
           <div class="no-poster" style="display:none;"><span class="material-symbols-rounded">image</span></div>`
        : `<div class="no-poster"><span class="material-symbols-rounded">image</span></div>`;
      const stagger = ` style="animation-delay:${Math.min(i * 30, 480)}ms"`;

      return `
        <div class="library-card animate-stagger" data-action="open-library-edit" data-id="${escapeAttr(e.id)}" data-title="${escapeAttr(e.title)}"${stagger}>
          <div class="library-card-poster">${posterHtml}</div>
          <div class="library-card-body">
            <div class="library-card-title" title="${escapeAttr(e.title)}">${escapeHtml(e.title)}</div>
            <div class="library-card-meta">
              <span class="library-card-status ${statusClass}">${statusLabel}</span>
              ${ratingHtml}
            </div>
            ${progressHtml}
          </div>
        </div>`;
    }).join("");

    // Fetch posters for cards without them
    fetchLibraryPosters();
  }

  function fetchLibraryPosters() {
    const cards = document.querySelectorAll("#library-grid .library-card[data-title]");
    const needPoster = Array.from(cards).filter(c => {
      const img = c.querySelector(".lib-poster");
      return !img || img.style.display === "none";
    });
    if (!needPoster.length) return;

    const titles = needPoster.map(c => c.dataset.title).filter(Boolean);
    if (!titles.length) return;

    const contentFilter = el("content_filter")?.value || "sfw";
    fetch("/api/recommend/posters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ titles, content_filter: contentFilter }),
    }).then(r => r.ok ? r.json() : {}).then(data => {
      needPoster.forEach(card => {
        const info = data[card.dataset.title];
        if (!info || !info.poster) return;
        const posterWrap = card.querySelector(".library-card-poster");
        if (!posterWrap) return;
        const noPoster = posterWrap.querySelector(".no-poster");
        const existingImg = posterWrap.querySelector(".lib-poster");

        if (existingImg && existingImg.style.display !== "none") return;

        if (existingImg) existingImg.remove();
        const img = document.createElement("img");
        img.className = "lib-poster";
        img.alt = card.dataset.title || "";
        img.loading = "lazy";
        img.src = info.poster;
        if (noPoster) noPoster.before(img);
        else posterWrap.prepend(img);
      });
    }).catch(err => logError("fetchLibraryPosters", err));
  }

  function filterLibrary(status) {
    _libraryFilter = status;
    document.querySelectorAll(".library-filters .chip").forEach(c => {
      c.classList.toggle("active", c.dataset.status === status);
    });
    loadLibrary();
  }

  function searchLibrary() {
    if (_librarySearchTimeout) clearTimeout(_librarySearchTimeout);
    _librarySearchTimeout = setTimeout(loadLibrary, 300);
  }

  function updateLibraryBadge(total) {
    const badge = el("library-badge");
    if (!badge) return;
    if (total > 0) { badge.style.display = ""; badge.textContent = total; }
    else { badge.style.display = "none"; }
  }

  function sortLibrary(sortBy) {
    _librarySort = sortBy;
    loadLibrary();
  }

  function toggleSortDirection() {
    _librarySortDesc = !_librarySortDesc;
    const btn = el("sort-dir-btn");
    if (btn) btn.classList.toggle("desc", _librarySortDesc);
    loadLibrary();
  }

  async function addToLibrary(animeData) {
    showToast("Fetching anime info from AniList...");
    // Enrich with AniList data
    const enriched = await enrichLibraryEntry(animeData);
    fetch("/api/library/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(enriched),
    }).then(r => r.json()).then(data => {
      if (data.error) {
        showToast(data.error);
      } else {
        showToast(`Added "${enriched.title}" to library`);
        updateLibraryBadge(data.stats?.total || 0);
        markRecCardSaved(enriched.title);
      }
    }).catch(err => {
      logError("addToLibrary", err);
      showToast("Failed to add to library");
    });
  }

  async function openLibraryEdit(entryId) {
    fetch("/api/library/" + entryId).then(async r => {
      const entry = await r.json();
      if (entry.error) { showToast(entry.error); return; }
      _libraryEditId = entryId;
      // Enrich with AniList data if missing
      const enriched = await enrichLibraryEntry(entry);
      showFullscreenDetail(enriched);
    }).catch(err => logError("openLibraryEdit", err));
  }

  function showFullscreenDetail(entry) {
    // Remove any existing fullscreen detail
    const existing = el("detail-fullscreen");
    if (existing) existing.remove();

    state.focusReturnEl = document.activeElement;

    const title = entry.title || "Unknown";
    const poster = entry.poster || "";
    const banner = entry.banner || "";
    const synopsis = entry.synopsis || entry.notes || "No description available.";
    const genres = (entry.genres || []).slice(0, 8).map(g => `<span class="chip chip-accent">${escapeHtml(g)}</span>`).join("");
    const themes = (entry.themes || []).slice(0, 8).map(t => `<span class="chip chip-soft">${escapeHtml(t)}</span>`).join("");

    const statusLabels = {
      watching: "Watching",
      completed: "Completed",
      plan_to_watch: "Plan to Watch",
      on_hold: "On Hold",
      dropped: "Dropped"
    };
    const statusLabel = statusLabels[entry.status] || entry.status || "Plan to Watch";

    const posterHtml = poster
      ? `<img class="detail-poster" src="${escapeAttr(poster)}" alt="${escapeAttr(title)}" loading="lazy"
           onload="this.parentElement.classList.add('loaded')"
           onerror="this.classList.add('no-src'); this.parentElement.classList.add('loaded')">`
      : `<div class="detail-poster no-src"></div>`;

    const bannerHtml = banner
      ? `<div class="detail-banner"><img src="${escapeAttr(banner)}" alt="Banner" loading="lazy"></div>`
      : "";

    const ratingHtml = entry.rating
      ? `<div class="info-item"><span class="info-label">My Rating</span><span class="info-value"><span class="material-symbols-rounded star-icon">star</span>${entry.rating}/10</span></div>`
      : "";

    const progressHtml = entry.total_episodes
      ? `<div class="info-item"><span class="info-label">Progress</span><span class="info-value">${entry.progress || 0}/${entry.total_episodes} episodes</span></div>`
      : `<div class="info-item"><span class="info-label">Episodes</span><span class="info-value">${entry.episodes || "Unknown"}</span></div>`;

    const studios = (entry.studios || []).join(", ") || "Unknown";
    const producers = (entry.producers || []).join(", ") || "Unknown";

    const charactersHtml = (entry.characters || []).slice(0, 12).map(c => `
      <div class="character-item">
        <div class="character-avatar">
          ${c.image ? `<img src="${escapeAttr(c.image)}" alt="${escapeAttr(c.name)}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
          <span class="material-symbols-rounded" style="display:none;">person</span>` : `<span class="material-symbols-rounded">person</span>`}
        </div>
        <div class="character-info">
          <span class="character-name">${escapeHtml(c.name)}</span>
          <span class="character-role muted">${escapeHtml(c.role || "Character")}</span>
        </div>
      </div>
    `).join("");

    const streamingHtml = (entry.streaming || []).map(s => `
      <a class="link-item streaming" href="${escapeAttr(s.url || '#')}" target="_blank" rel="noreferrer">
        <span class="material-symbols-rounded">play_circle</span> ${escapeHtml(s.site || s.title || "Stream")}
      </a>
    `).join("");

    const externalLinksHtml = (entry.external_links || []).map(l => `
      <a class="link-item" href="${escapeAttr(l.url)}" target="_blank" rel="noreferrer">
        <span class="material-symbols-rounded">open_in_new</span> ${escapeHtml(l.site || "Link")}
      </a>
    `).join("");

    const html = `
      <div class="detail-fullscreen" id="detail-fullscreen">
        <div class="detail-fullscreen-header">
          <button class="detail-fullscreen-back" data-action="close-fullscreen" aria-label="Back to library">
            <span class="material-symbols-rounded">arrow_back</span>
          </button>
          <span class="detail-fullscreen-title">${escapeHtml(title)}</span>
          <div class="detail-fullscreen-actions">
            <button class="btn btn-outlined btn-small" data-action="refresh-anilist" data-title="${escapeAttr(title)}">
              <span class="material-symbols-rounded">refresh</span> Refresh
            </button>
            <button class="btn btn-outlined btn-small" data-action="edit-from-fullscreen" data-id="${escapeAttr(entry.id || _libraryEditId)}">
              <span class="material-symbols-rounded">edit</span> Edit
            </button>
          </div>
        </div>
        <div class="detail-fullscreen-scroll">
          ${bannerHtml}
          <div class="detail-hero">
            <div class="detail-poster-wrap">
              ${posterHtml}
              <div class="detail-poster-placeholder">
                <span class="material-symbols-rounded">image</span>
              </div>
            </div>
            <div class="detail-hero-info">
              <h1 class="title-display" style="font-size:1.8rem;">${escapeHtml(title)}</h1>
              ${entry.title_english && entry.title_english !== title ? `<p class="muted" style="font-size:1rem;">${escapeHtml(entry.title_english)}</p>` : ""}
              ${entry.title_japanese ? `<p class="muted" style="font-size:0.9rem;">${escapeHtml(entry.title_japanese)}</p>` : ""}
              <div class="chips-wrap" style="margin-top:12px;">${genres}${themes}</div>
              <div class="detail-hero-actions" style="margin-top:16px;">
                <button class="btn btn-filled" data-action="edit-from-fullscreen" data-id="${escapeAttr(entry.id || _libraryEditId)}">
                  <span class="material-symbols-rounded">edit</span> Edit Entry
                </button>
                <button class="btn btn-danger" data-action="remove-from-fullscreen" data-id="${escapeAttr(entry.id || _libraryEditId)}">
                  <span class="material-symbols-rounded">delete</span> Remove
                </button>
              </div>
            </div>
          </div>

          ${entry.notes ? `<div class="card card-filled details-card"><h3 class="card-title"><span class="material-symbols-rounded">notes</span> My Notes</h3><p class="synopsis">${escapeHtml(entry.notes)}</p></div>` : ""}

          <div class="card card-filled details-card">
            <h3 class="card-title"><span class="material-symbols-rounded">bar_chart</span> Library Info</h3>
            <div class="info-grid">
              <div class="info-item"><span class="info-label">Status</span><span class="info-value library-card-status status-${entry.status || 'plan_to_watch'}">${statusLabel}</span></div>
              ${ratingHtml}
              ${progressHtml}
              <div class="info-item"><span class="info-label">Type</span><span class="info-value">${escapeHtml(entry.type || "Unknown")}</span></div>
              <div class="info-item"><span class="info-label">Score</span><span class="info-value">${entry.score || "N/A"}</span></div>
              <div class="info-item"><span class="info-label">Studios</span><span class="info-value">${escapeHtml(studios)}</span></div>
              <div class="info-item"><span class="info-label">Producers</span><span class="info-value">${escapeHtml(producers)}</span></div>
              ${entry.duration ? `<div class="info-item"><span class="info-label">Duration</span><span class="info-value">${entry.duration} min</span></div>` : ""}
              ${entry.popularity ? `<div class="info-item"><span class="info-label">Popularity</span><span class="info-value">#${entry.popularity.toLocaleString()}</span></div>` : ""}
              ${entry.favorites ? `<div class="info-item"><span class="info-label">Favorites</span><span class="info-value">${entry.favorites.toLocaleString()}</span></div>` : ""}
            </div>
          </div>

          ${synopsis && synopsis !== "No description available." ? `<div class="card card-filled details-card"><h3 class="card-title"><span class="material-symbols-rounded">article</span> Synopsis</h3><p class="synopsis">${escapeHtml(synopsis)}</p></div>` : ""}

          ${charactersHtml ? `<div class="card card-filled details-card"><h3 class="card-title"><span class="material-symbols-rounded">people</span> Characters</h3><div class="character-grid">${charactersHtml}</div></div>` : ""}

          ${(streamingHtml || externalLinksHtml) ? `
          <div class="card card-filled details-card">
            <h3 class="card-title"><span class="material-symbols-rounded">link</span> Links</h3>
            <div class="link-grid">${streamingHtml}${externalLinksHtml}</div>
          </div>` : ""}

          <div class="detail-actions-bar">
            ${entry.url ? `<a class="btn btn-outlined" href="${escapeAttr(entry.url)}" target="_blank" rel="noreferrer"><span class="material-symbols-rounded">open_in_new</span> View on MAL</a>` : ""}
          </div>
        </div>
      </div>`;

    document.body.insertAdjacentHTML("beforeend", html);
    document.body.style.overflow = "hidden";

    // Focus first focusable element
    const fullscreen = el("detail-fullscreen");
    if (fullscreen) {
      const firstFocusable = fullscreen.querySelector(FOCUSABLE);
      if (firstFocusable) firstFocusable.focus();
    }
  }

  function closeFullscreenDetail() {
    const fullscreen = el("detail-fullscreen");
    if (fullscreen) {
      fullscreen.classList.add("view-exit");
      setTimeout(() => {
        fullscreen.remove();
        document.body.style.overflow = "";
        if (state.focusReturnEl && typeof state.focusReturnEl.focus === "function") {
          state.focusReturnEl.focus();
          state.focusReturnEl = null;
        }
      }, 250);
    }
  }

  function openEditFromFullscreen(entryId) {
    closeFullscreenDetail();
    setTimeout(() => {
      _libraryEditId = entryId;
      const modal = el("library-edit-modal");
      if (!modal) return;
      // Fetch entry data and populate modal
      fetch("/api/library/" + entryId).then(r => r.json()).then(entry => {
        if (entry.error) { showToast(entry.error); return; }
        el("lib-edit-title").textContent = entry.title;
        el("lib-edit-status").value = entry.status || "plan_to_watch";
        el("lib-edit-rating").value = entry.rating ?? "";
        el("lib-edit-progress").value = entry.progress || 0;
        el("lib-edit-notes").value = entry.notes || "";
        openDialog(modal);
        const saveBtn = el("lib-edit-save");
        const removeBtn = el("lib-edit-remove");
        if (saveBtn) saveBtn.onclick = saveLibraryEdit;
        if (removeBtn) removeBtn.onclick = removeLibraryEntry;
      }).catch(err => logError("openEditFromFullscreen", err));
    }, 300);
  }

  function removeFromFullscreen(entryId) {
    if (!confirm("Remove from library?")) return;
    fetch("/api/library/remove/" + entryId, { method: "POST" })
      .then(r => r.json()).then(data => {
        if (data.error) showToast(data.error);
        else {
          showToast("Removed from library");
          closeFullscreenDetail();
          loadLibrary();
        }
      }).catch(err => {
        logError("removeFromFullscreen", err);
        showToast("Failed to remove entry");
      });
  }

  function saveLibraryEdit() {
    if (!_libraryEditId) return;
    const payload = {
      status: el("lib-edit-status").value,
      rating: el("lib-edit-rating").value,
      progress: el("lib-edit-progress").value,
      notes: el("lib-edit-notes").value,
    };
    fetch("/api/library/update/" + _libraryEditId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(r => r.json()).then(data => {
      if (data.error) showToast(data.error);
      else { showToast("Updated!"); closeLibraryEdit(); loadLibrary(); }
    }).catch(err => {
      logError("saveLibraryEdit", err);
      showToast("Failed to update entry");
    });
  }

  function removeLibraryEntry() {
    if (!_libraryEditId || !confirm("Remove from library?")) return;
    fetch("/api/library/remove/" + _libraryEditId, { method: "POST" })
      .then(r => r.json()).then(data => {
        if (data.error) showToast(data.error);
        else { showToast("Removed from library"); closeLibraryEdit(); loadLibrary(); }
      }).catch(err => {
        logError("removeLibraryEntry", err);
        showToast("Failed to remove entry");
      });
  }

  function closeLibraryEdit() {
    closeDialog(el("library-edit-modal"));
    _libraryEditId = null;
  }

  function addToLibraryFromProfile() {
    const profileData = el("profile-data");
    if (!profileData) return;
    let profile;
    try {
      profile = JSON.parse(profileData.dataset.profile);
    } catch (e) {
      logError("profile-data parse", e);
      showToast("Could not read profile data");
      return;
    }
    const title = profile.titles?.all?.[0] || profile.titles?.english?.[0] || "";
    if (!title) { showToast("No title available to add"); return; }
    addToLibrary({
      title,
      title_english: profile.titles?.english?.[0] || "",
      title_japanese: profile.titles?.japanese?.[0] || "",
      poster: profile.media?.poster || "",
      banner: profile.media?.banner || "",
      score: profile.statistics?.jikan?.score || profile.statistics?.anilist?.average_score,
      episodes: profile.release?.jikan?.episodes,
      type: profile.release?.jikan?.type || "TV",
      genres: profile.genres || [],
      themes: profile.themes || [],
      url: profile.external_links?.[0]?.url || "",
    });
  }

  function addToLibraryFromDetail(btnEl) {
    try {
      const data = JSON.parse(btnEl.dataset.detailJson);
      addToLibrary(data);
      closeDetail();
    } catch (e) {
      logError("detail-json parse", e);
      showToast("Could not add to library");
    }
  }

  function markRecCardSaved(title) {
    document.querySelectorAll(".rec-card[data-title]").forEach(card => {
      if (card.dataset.title === title) {
        const btn = card.querySelector(".rec-save-btn");
        if (btn) { btn.classList.add("saved"); btn.innerHTML = '<span class="material-symbols-rounded">check</span> Saved'; }
      }
    });
  }

  function exportLibrary() {
    fetch("/api/library/export").then(r => r.json()).then(data => {
      if (data.error) { showToast("Export failed: " + data.error); return; }
      const json = JSON.stringify(data.entries || [], null, 2);
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "smart-fox-library-" + new Date().toISOString().slice(0, 10) + ".json";
      a.click();
      URL.revokeObjectURL(url);
      showToast("Library exported (" + (data.entries?.length || 0) + " entries)");
    }).catch(err => showToast("Export failed: " + err));
  }

  function openImportModal() { openDialog(el("import-modal")); }
  function closeImportModal() {
    closeDialog(el("import-modal"));
    const text = el("import-text");
    if (text) text.value = "";
  }

  function handleImportFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      const text = el("import-text");
      if (text) text.value = e.target.result;
    };
    reader.readAsText(file);
    event.target.value = "";
  }

  function executeImport() {
    const text = el("import-text")?.value?.trim();
    const mode = el("import-mode")?.value || "merge";
    if (!text) { showToast("Paste library JSON or select a file"); return; }
    let entries;
    try { entries = JSON.parse(text); } catch (e) { showToast("Invalid JSON: " + e.message); return; }
    if (!Array.isArray(entries)) { showToast("JSON must be an array of entries"); return; }
    fetch("/api/library/import?mode=" + mode, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entries),
    }).then(r => r.json()).then(data => {
      if (data.error) { showToast("Import failed: " + data.error); return; }
      closeImportModal();
      loadLibrary();
      showToast("Imported " + (data.imported || 0) + " entries, skipped " + (data.skipped || 0));
    }).catch(err => showToast("Import failed: " + err));
  }

  /* ── Empty State Renderer ── */
  function renderEmptyState(icon, title, subtitle) {
    return `<div class="empty-state">
      <span class="material-symbols-rounded">${icon}</span>
      <p>${escapeHtml(title)}</p>
      <span class="muted">${escapeHtml(subtitle)}</span>
    </div>`;
  }

  /* ── Settings ── */
  function openSettings() {
    const modal = el("settings-modal");
    if (!modal) return;
    openDialog(modal);
    loadSettingsForm();
    updateTokenUsage();
    if (!window._tokenPollInterval) window._tokenPollInterval = setInterval(updateTokenUsage, 3000);
  }
  function closeSettings() {
    closeDialog(el("settings-modal"));
    if (window._tokenPollInterval) { clearInterval(window._tokenPollInterval); window._tokenPollInterval = null; }
  }

  function loadSettingsForm() {
    fetch("/api/config").then(r => r.json()).then(cfg => {
      setVal("cfg-local-base-url", cfg.local_ai_base_url);
      setVal("cfg-local-model", cfg.local_ai_model);
      setVal("cfg-local-api-key", cfg.has_local_api_key ? "••••••••" : "");
      setVal("cfg-local-parallel", cfg.local_ai_parallel_slots);
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
    }).catch(err => logError("loadSettingsForm", err));
  }

  function setVal(id, val) { const node = el(id); if (node && val !== undefined && val !== null) node.value = val; }
  function getVal(id) { return el(id)?.value || ""; }

  function toggleProviderSections(provider) {
    const ls = el("settings-local-section");
    const os = el("settings-openrouter-section");
    const ms = el("settings-model-status-section");
    if (ls) ls.style.display = provider === "local" ? "" : "none";
    if (os) os.style.display = provider === "openrouter" ? "" : "none";
    if (ms) ms.style.display = provider === "openrouter" ? "" : "none";
  }

  function collectSettings() {
    return {
      ai_provider: document.querySelector('input[name="ai_provider"]:checked')?.value || "local",
      local_ai_base_url: getVal("cfg-local-base-url"),
      local_ai_model: getVal("cfg-local-model"),
      local_ai_api_key: getVal("cfg-local-api-key"),
      local_ai_parallel_slots: parseInt(getVal("cfg-local-parallel")) || 1,
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

  function saveSettings() {
    const status = el("settings-status");
    const payload = collectSettings();
    fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(r => r.json()).then(data => {
      if (data.error) { if (status) { status.textContent = "Error: " + data.error; status.className = "settings-status error"; } }
      else { if (status) { status.textContent = "Saved!"; status.className = "settings-status ok"; } updateTokenUsage(); setTimeout(() => { if (status) status.textContent = ""; }, 2000); }
    }).catch(err => { if (status) { status.textContent = "Save failed: " + err; status.className = "settings-status error"; } });
  }

  function updateTokenUsage() {
    fetch("/api/tokens/usage").then(r => r.json()).then(data => {
      const textEl = el("token-usage-text");
      const pctEl = el("token-usage-pct");
      const fillEl = el("token-usage-fill");
      if (!textEl) return;
      const total = data.total_tokens || 0;
      const calls = data.calls || 0;
      const pct = data.budget_used_pct || 0;
      textEl.textContent = `${total.toLocaleString()} tokens used (${calls} calls)`;
      if (pctEl) pctEl.textContent = `${pct}%`;
      if (fillEl) {
        fillEl.style.width = `${Math.min(pct, 100)}%`;
        fillEl.className = "progress-bar token-fill";
        if (pct >= 100) fillEl.classList.add("over-budget");
        else if (pct >= 80) fillEl.classList.add("near-budget");
        else fillEl.classList.add("ok");
      }
      const setNum = (id, val) => { const node = el(id); if (node) node.textContent = (val || 0).toLocaleString(); };
      setNum("token-prompt", data.prompt_tokens);
      setNum("token-completion", data.completion_tokens);
      setNum("token-total", total);
      setNum("token-calls", calls);
    }).catch(err => logError("updateTokenUsage", err));
  }

  function testConnection() {
    const status = el("settings-status");
    if (status) { status.textContent = "Testing connection..."; status.className = "settings-status"; }
    fetch("/api/test-connection", { method: "POST" }).then(r => r.json()).then(data => {
      if (data.status === 200) { if (status) { status.textContent = "Connection OK (200)"; status.className = "settings-status ok"; } }
      else { if (status) { status.textContent = "Failed (" + (data.status || "error") + "): " + (data.error || data.body || ""); status.className = "settings-status error"; } }
    }).catch(err => { if (status) { status.textContent = "Test failed: " + err; status.className = "settings-status error"; } });
  }

  function resetDefaults() {
    if (!confirm("Reset all settings to defaults?")) return;
    fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })
      .then(r => r.json()).then(() => { loadSettingsForm(); showToast("Settings reset to defaults"); });
  }

  function refreshModelStatus() {
    const grid = el("model-status-grid");
    if (!grid) return;
    grid.innerHTML = '<div class="muted center">Checking models...</div>';
    fetch("/api/models/status").then(r => r.json()).then(data => {
      if (data.error) { grid.innerHTML = `<div class="model-status-error">${escapeHtml(data.error)}</div>`; return; }
      const models = data.models || [];
      if (!models.length) { grid.innerHTML = '<div class="muted center">No models configured</div>'; return; }
      grid.innerHTML = models.map(m => {
        const cls = m.status === "ok" ? "online" : m.status === "rate_limited" ? "limited" : "offline";
        const label = m.status === "ok" ? "Available" : m.status === "rate_limited" ? "Rate Limited" : "Error";
        const latency = m.latency_ms ? `${m.latency_ms}ms` : "";
        const badge = m.is_primary ? '<span class="model-badge-primary">Primary</span>' : "";
        const err = m.error ? `<span title="${escapeAttr(m.error)}">!</span>` : "";
        return `<div class="model-status-card ${cls}"><div class="model-status-dot"></div><div style="flex:1;min-width:0;"><div class="model-status-name">${escapeHtml(m.model)} ${badge}</div><div class="model-status-meta">${label} ${latency} ${err}</div></div></div>`;
      }).join("");
    }).catch(err => { grid.innerHTML = `<div class="model-status-error">Failed: ${escapeHtml(String(err))}</div>`; });
  }

  /* ── Dialog helpers (focus management) ── */
  const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

  function openDialog(modal) {
    if (!modal) return;
    state.focusReturnEl = document.activeElement;
    modal.style.display = "flex";
    modal.setAttribute("data-open", "");
    const focusables = modal.querySelectorAll(FOCUSABLE);
    if (focusables.length) focusables[0].focus();
  }

  function closeDialog(modal) {
    if (!modal) return;
    modal.style.display = "none";
    modal.removeAttribute("data-open");
    if (state.focusReturnEl && typeof state.focusReturnEl.focus === "function") {
      state.focusReturnEl.focus();
      state.focusReturnEl = null;
    }
  }

  function anyDialogOpen() {
    return document.querySelectorAll('.dialog-scrim[data-open]').length > 0;
  }

  function trapFocus(e) {
    const open = document.querySelector('.dialog-scrim[data-open]');
    if (!open || e.key !== "Tab") return;
    const focusables = open.querySelectorAll(FOCUSABLE);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  /* ── Event Dispatch (delegated via data-action) ── */
  const ACTIONS = {
    "show-view": (target) => showView(target.dataset.view),
    "add-from-profile": () => addToLibraryFromProfile(),
    "add-from-profile-data": (target) => {
      try {
        const data = JSON.parse(target.dataset.profile);
        addToLibrary({
          title: data.titles?.all?.[0] || data.titles?.english?.[0] || "",
          title_english: data.titles?.english?.[0] || "",
          title_japanese: data.titles?.japanese?.[0] || "",
          poster: data.media?.poster || "",
          banner: data.media?.banner || "",
          score: data.statistics?.jikan?.score || data.statistics?.anilist?.average_score,
          episodes: data.release?.jikan?.episodes,
          type: data.release?.jikan?.type || "TV",
          genres: data.genres || [],
          themes: data.themes || [],
          url: data.external_links?.[0]?.url || "",
        });
      } catch (e) {
        logError("add-from-profile-data parse", e);
        showToast("Could not add to library");
      }
    },
    "add-from-detail": (target) => addToLibraryFromDetail(target),
    "share-recs": () => shareRecommendations(),
    "show-detail-card": (target) => showDetailFromCard(target),
    "save-from-card": (target) => saveFromCard(target),
    "filter-library": (target) => {
      const status = target.dataset.status;
      if (status) {
        showSubPage(status);
      } else {
        filterLibrary("");
      }
    },
    "open-sub-page": (target) => showSubPage(target.dataset.status),
    "close-sub-page": () => hideSubPage(),
    "open-library-edit": (target) => openLibraryEdit(target.dataset.id),
    "close-fullscreen": () => closeFullscreenDetail(),
    "edit-from-fullscreen": (target) => openEditFromFullscreen(target.dataset.id),
    "remove-from-fullscreen": (target) => removeFromFullscreen(target.dataset.id),
    "refresh-anilist": async (target) => {
      const title = target.dataset.title;
      if (!title) return;
      showToast("Refreshing from AniList...");
      // Clear cache for this title
      const cache = getCache();
      delete cache[title.toLowerCase().trim()];
      setCache(cache);
      // Re-fetch
      const data = await fetchAnimeFromAniList(title);
      if (data) {
        showToast("Updated from AniList");
        // Re-open with fresh data
        const entryId = _libraryEditId;
        if (entryId) {
          fetch("/api/library/" + entryId).then(r => r.json()).then(async entry => {
            const enriched = await enrichLibraryEntry(entry);
            showFullscreenDetail(enriched);
          });
        }
      } else {
        showToast("Failed to fetch from AniList");
      }
    },
    "export-library": () => exportLibrary(),
    "open-import": () => openImportModal(),
    "close-import": () => closeImportModal(),
    "execute-import": () => executeImport(),
    "close-detail": () => closeDetail(),
    "close-settings": () => closeSettings(),
    "close-library-edit": () => closeLibraryEdit(),
    "db-tab": (target) => dbTabSwitch(target.dataset.dbTab),
    "db-search": () => dbSearch(),
    "db-page": (target) => dbPageGo(target.dataset.page),
    "db-detail": (target) => dbShowDetail(target.dataset.id, target.dataset.type),
    "db-close-detail": () => dbCloseDetail(),
    "db-back": () => dbCloseDetail(),
  };

  function dispatchAction(target, action) {
    if (!action) return false;
    const handler = ACTIONS[action];
    if (handler) { handler(target); return true; }
    return false;
  }

  /* ── Database View ── */
  let _dbTab = "anime";
  let _dbPage = 1;
  let _dbQuery = "";
  let _dbDetailVisible = false;

  function dbTabSwitch(tab) {
    _dbTab = tab;
    document.querySelectorAll(".db-tab").forEach(t => t.classList.toggle("active", t.dataset.dbTab === tab));
    // Update source options based on tab
    const src = el("db-source");
    if (src) {
      if (tab === "studios") {
        src.innerHTML = '<option value="anilist">AniList</option>';
      } else if (tab === "manga") {
        src.innerHTML = '<option value="anilist">AniList</option><option value="kitsu">Kitsu</option>';
      } else if (tab === "characters") {
        src.innerHTML = '<option value="anilist">AniList</option><option value="kitsu">Kitsu</option>';
      } else {
        src.innerHTML = '<option value="anilist">AniList</option><option value="jikan">Jikan (MAL)</option><option value="kitsu">Kitsu</option><option value="anidb">AniDB</option>';
      }
    }
    dbSearch();
  }

  async function dbSearch() {
    const query = el("db-search-input")?.value?.trim() || "";
    const source = el("db-source")?.value || "anilist";
    _dbQuery = query;
    _dbPage = 1;
    const grid = el("db-grid");
    const pagination = el("db-pagination");
    if (!grid) return;
    grid.innerHTML = '<div class="db-loading"><div class="circular-progress"></div></div>';
    if (pagination) pagination.innerHTML = "";
    try {
      const endpoint = _dbTab === "characters" ? "characters" : _dbTab === "studios" ? "studios" : _dbTab;
      const url = `/api/db/${endpoint}?q=${encodeURIComponent(query)}&source=${source}&page=${_dbPage}`;
      const resp = await fetch(url);
      const data = await resp.json();
      if (data.error) {
        grid.innerHTML = `<div class="empty-state"><p>${escapeHtml(data.error)}</p></div>`;
        return;
      }
      renderDbResults(data.items || []);
      if (data.total && data.total > 20) {
        renderDbPagination(data.total);
      }
    } catch (err) {
      logError("dbSearch", err);
      grid.innerHTML = '<div class="empty-state"><p>Search failed</p></div>';
    }
  }

  function renderDbResults(items) {
    const grid = el("db-grid");
    if (!grid) return;
    if (!items.length) {
      grid.innerHTML = '<div class="empty-state"><span class="material-symbols-rounded">search_off</span><p>No results found</p></div>';
      return;
    }
    let html = "";
    let stagger = 0;
    for (const item of items) {
      const poster = item.poster || "";
      const title = item.title || item.name || "Unknown";
      const subtitle = item.type || item.status || "";
      const score = item.score ? `<span class="db-item-score">${item.score}%</span>` : "";
      const episodes = item.episodes ? `<span class="db-item-ep">${item.episodes} ep</span>` : "";
      const chapters = item.chapters ? `<span class="db-item-ep">${item.chapters} ch</span>` : "";
      const dataJson = escapeAttr(JSON.stringify({ id: item.id, title: title, type: _dbTab }));
      html += `
        <div class="db-item-card animate-stagger" data-action="db-detail" data-id="${item.id}" data-type="${_dbTab}"${stagger ? ` style="--stagger:${stagger}"` : ""}>
          <div class="db-item-poster">${poster ? `<img src="${escapeAttr(poster)}" alt="${escapeAttr(title)}" loading="lazy" onerror="this.parentElement.innerHTML='<span class=\\'material-symbols-rounded poster-fallback\\'>${_dbTab === 'studios' ? 'business' : _dbTab === 'characters' ? 'person' : 'movie'}</span>'">` : `<span class="material-symbols-rounded poster-fallback">${_dbTab === "studios" ? "business" : _dbTab === "characters" ? "person" : "movie"}</span>`}</div>
          <div class="db-item-info">
            <div class="db-item-title">${escapeHtml(title)}</div>
            <div class="db-item-meta">${escapeHtml(subtitle)} ${score} ${episodes} ${chapters}</div>
          </div>
        </div>`;
      stagger++;
    }
    grid.innerHTML = html;
  }

  function renderDbPagination(total) {
    const pagination = el("db-pagination");
    if (!pagination) return;
    const pages = Math.ceil(total / 20);
    let html = "";
    for (let i = 1; i <= Math.min(pages, 10); i++) {
      html += `<button class="btn ${i === _dbPage ? 'btn-filled' : 'btn-outlined'} btn-small" data-action="db-page" data-page="${i}">${i}</button>`;
    }
    pagination.innerHTML = html;
  }

  function dbPageGo(page) {
    _dbPage = parseInt(page) || 1;
    dbSearch();
  }

  async function dbShowDetail(id, type) {
    const source = el("db-source")?.value || "anilist";
    const grid = el("db-grid");
    const pagination = el("db-pagination");
    const detailPage = el("db-detail-page");
    if (!grid || !detailPage) return;
    grid.style.display = "none";
    pagination?.style && (pagination.style.display = "none");
    detailPage.style.display = "block";
    _dbDetailVisible = true;
    detailPage.innerHTML = '<div class="db-loading"><div class="circular-progress"></div></div>';

    try {
      const endpoint = type === "characters" ? "character" : type === "studios" ? "studio" : type;
      const resp = await fetch(`/api/db/${endpoint}/${id}?source=${source}`);
      const data = await resp.json();
      if (data.error) {
        detailPage.innerHTML = `<div class="empty-state"><p>${escapeHtml(data.error)}</p></div>`;
        return;
      }
      detailPage.innerHTML = renderDbDetail(data, type);
    } catch (err) {
      logError("dbShowDetail", err);
      detailPage.innerHTML = '<div class="empty-state"><p>Failed to load details</p></div>';
    }
  }

  function renderDbDetail(data, type) {
    const poster = data.poster || data.image || "";
    const banner = data.banner || "";
    const title = data.title || data.name || "Unknown";
    const titleEn = data.title_english || "";
    const titleJp = data.title_japanese || "";
    const synopsis = data.synopsis || data.description || "";
    const score = data.score || data.mean_score;
    const genres = data.genres || [];
    const themes = data.themes || [];
    const episodes = data.episodes;
    const chapters = data.chapters;
    const volumes = data.volumes;
    const status = data.status || "";
    const studios = data.studios || [];
    const characters = data.characters || [];
    const anime = data.anime || [];
    const relations = data.relations || [];
    const url = data.url || "";

    let html = `
      <div class="db-detail" style="--banner:url('${escapeAttr(banner)}')">
        <button class="btn btn-outlined db-detail-back" data-action="db-back">
          <span class="material-symbols-rounded">arrow_back</span> Back
        </button>
        <div class="db-detail-header">
          ${poster ? `<img class="db-detail-poster" src="${escapeAttr(poster)}" alt="${escapeAttr(title)}" onerror="this.style.display='none'">` : ''}
          <div class="db-detail-info">
            <h2 class="db-detail-title">${escapeHtml(title)}</h2>
            ${titleEn ? `<div class="db-detail-subtitle">${escapeHtml(titleEn)}</div>` : ''}
            ${titleJp ? `<div class="db-detail-subtitle">${escapeHtml(titleJp)}</div>` : ''}
            <div class="db-detail-meta">
              ${status ? `<span class="chip chip-soft">${escapeHtml(status)}</span>` : ''}
              ${score ? `<span class="chip chip-accent">${score}%</span>` : ''}
              ${episodes ? `<span class="chip chip-soft">${episodes} episodes</span>` : ''}
              ${chapters ? `<span class="chip chip-soft">${chapters} chapters</span>` : ''}
              ${volumes ? `<span class="chip chip-soft">${volumes} volumes</span>` : ''}
            </div>
            ${genres.length ? `<div class="db-detail-genres">${genres.map(g => `<span class="chip chip-soft">${escapeHtml(g)}</span>`).join('')}</div>` : ''}
            ${themes.length ? `<div class="db-detail-genres">${themes.map(t => `<span class="chip chip-soft">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
            ${url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener" class="btn btn-outlined btn-small"><span class="material-symbols-rounded">open_in_new</span> View Source</a>` : ''}
          </div>
        </div>
        ${synopsis ? `<div class="db-detail-synopsis"><h3>Synopsis</h3><p>${escapeHtml(synopsis)}</p></div>` : ''}
        ${studios.length ? `<div class="db-detail-section"><h3>Studios</h3><div class="db-detail-chips">${studios.map(s => `<span class="chip chip-soft">${escapeHtml(s)}</span>`).join('')}</div></div>` : ''}
        ${characters.length ? `<div class="db-detail-section"><h3>Characters</h3><div class="db-character-grid">${characters.slice(0, 12).map(c => `
          <div class="db-character-card">
            ${c.image ? `<img src="${escapeAttr(c.image)}" alt="${escapeAttr(c.name)}" loading="lazy" onerror="this.style.display='none'">` : ''}
            <div class="db-character-name">${escapeHtml(c.name)}</div>
            ${c.role ? `<div class="db-character-role">${escapeHtml(c.role)}</div>` : ''}
          </div>`).join('')}</div></div>` : ''}
        ${anime.length ? `<div class="db-detail-section"><h3>Anime</h3><div class="db-anime-grid">${anime.slice(0, 12).map(a => `
          <div class="db-anime-card" data-action="db-detail" data-id="${a.id}" data-type="anime">
            ${a.poster ? `<img src="${escapeAttr(a.poster)}" alt="${escapeAttr(a.title)}" loading="lazy" onerror="this.style.display='none'">` : ''}
            <div class="db-anime-title">${escapeHtml(a.title)}</div>
            ${a.score ? `<div class="db-anime-score">${a.score}%</div>` : ''}
          </div>`).join('')}</div></div>` : ''}
        ${relations.length ? `<div class="db-detail-section"><h3>Relations</h3><div class="db-relation-grid">${relations.map(r => `
          <div class="db-relation-card">
            <span class="db-relation-type">${escapeHtml(r.relation || '')}</span>
            <span class="db-relation-name">${escapeHtml(r.name || '')}</span>
            <span class="db-relation-mtype">${escapeHtml(r.type || '')}</span>
          </div>`).join('')}</div></div>` : ''}
      </div>`;
    return html;
  }

  function dbCloseDetail() {
    const grid = el("db-grid");
    const pagination = el("db-pagination");
    const detailPage = el("db-detail-page");
    if (grid) grid.style.display = "";
    if (pagination) pagination.style.display = "";
    if (detailPage) { detailPage.style.display = "none"; detailPage.innerHTML = ""; }
    _dbDetailVisible = false;
  }

  /* ── Initialization ── */
  function init() {
    initTheme();

    // Theme toggle
    const themeToggle = el("theme-toggle");
    if (themeToggle) themeToggle.addEventListener("click", toggleTheme);

    // Global click delegation for [data-action]
    document.addEventListener("click", (e) => {
      const actionEl = e.target.closest("[data-action]");
      if (!actionEl) return;
      const action = actionEl.dataset.action;
      if (dispatchAction(actionEl, action)) e.preventDefault();
    });

    // Search form submit
    const searchForm = document.querySelector("[data-loading-form]");
    if (searchForm) {
      searchForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (state.searchInProgress) { showToast("A search is already running"); return; }
        const query = el("query").value.trim();
        const description = el("description").value.trim();
        const contentFilter = el("content_filter").value;
        const negativePrompt = el("negative_prompt").value.trim();
        if (!query && !description) { showToast("Enter an anime title or description"); return; }
        startSearch(query, description, contentFilter, negativePrompt);
      });
    }

    // Library search input (debounced)
    const librarySearch = el("library-search");
    if (librarySearch) librarySearch.addEventListener("input", searchLibrary);

    // Library sort dropdown + direction toggle
    const librarySort = el("library-sort");
    if (librarySort) librarySort.addEventListener("change", () => sortLibrary(librarySort.value));
    const sortDirBtn = el("sort-dir-btn");
    if (sortDirBtn) sortDirBtn.addEventListener("click", toggleSortDirection);

    // Settings: open/close + controls
    const gear = el("settings-toggle");
    if (gear) gear.addEventListener("click", openSettings);
    document.querySelectorAll('input[name="ai_provider"]').forEach(radio => {
      radio.addEventListener("change", () => {
        document.querySelectorAll(".provider-card").forEach(c => c.classList.remove("active"));
        radio.closest(".provider-card")?.classList.add("active");
        toggleProviderSections(radio.value);
        saveSettings();
      });
    });
    const testBtn = el("settings-test");
    if (testBtn) testBtn.addEventListener("click", testConnection);
    const resetBtn = el("settings-reset");
    if (resetBtn) resetBtn.addEventListener("click", resetDefaults);
    const refreshBtn = el("settings-refresh-models");
    if (refreshBtn) refreshBtn.addEventListener("click", refreshModelStatus);

    // Settings auto-save (debounced) — but only for actual content changes
    let saveTimeout;
    const settingsModal = el("settings-modal");
    if (settingsModal) {
      const onSettingsChange = (e) => {
        // Don't auto-save while the placeholder mask is in password fields
        if (e.target && e.target.type === "password" && /•/.test(e.target.value)) return;
        clearTimeout(saveTimeout);
        saveTimeout = setTimeout(saveSettings, 500);
      };
      settingsModal.addEventListener("input", onSettingsChange);
      settingsModal.addEventListener("change", onSettingsChange);
    }

    // Cancel search button
    const cancelBtn = el("cancel-search-btn");
    if (cancelBtn) cancelBtn.addEventListener("click", cancelSearch);

    // Import file picker
    const importChoose = el("import-choose-file");
    const importFile = el("import-file");
    if (importChoose && importFile) {
      importChoose.addEventListener("click", () => importFile.click());
      importFile.addEventListener("change", handleImportFile);
    }

    // Database view
    const dbSearchBtn = el("db-search-btn");
    const dbSearchInput = el("db-search-input");
    if (dbSearchBtn) dbSearchBtn.addEventListener("click", () => dbSearch());
    if (dbSearchInput) dbSearchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") dbSearch(); });

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
      // Escape closes fullscreen, then dialogs
      if (e.key === "Escape") {
        const fullscreen = el("detail-fullscreen");
        if (fullscreen) {
          closeFullscreenDetail();
          return;
        }
        if (anyDialogOpen()) {
          const open = document.querySelector('.dialog-scrim[data-open]');
          if (open) closeDialog(open);
          return;
        }
      }
      // Focus trap inside open dialog or fullscreen
      if (anyDialogOpen()) { trapFocus(e); return; }
      const fullscreen = el("detail-fullscreen");
      if (fullscreen && e.key === "Tab") {
        const focusables = fullscreen.querySelectorAll(FOCUSABLE);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault(); first.focus();
        }
        return;
      }

      // Don't hijack keys when typing in a field
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");

      // "/" or Ctrl+K focuses search
      if (!typing && (e.key === "/" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k"))) {
        e.preventDefault();
        const q = el("query");
        if (q && !state.searchInProgress) {
          showView("search");
          q.focus();
        }
      }
      // Ctrl+L switches to library
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "l") {
        e.preventDefault();
        showView("library");
      }
    });

    // Initial badge count
    updateLibraryBadge(0);
  }

  // Run init on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
