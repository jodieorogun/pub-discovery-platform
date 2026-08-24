const form = document.querySelector("#search-form");
const queryInput = document.querySelector("#query");
const searchButton = document.querySelector("#search-button");
const resultsContainer = document.querySelector("#results");
const statusElement = document.querySelector("#status");
const summaryElement = document.querySelector("#result-summary");
const resultsTitle = document.querySelector("#results-title");
const loadMoreButton = document.querySelector("#load-more");
const activeFilters = document.querySelector("#active-filters");
const areaFilter = document.querySelector("#area-filter");
const applyFiltersButton = document.querySelector("#apply-filters");
const listViewButton = document.querySelector("#list-view");
const mapViewButton = document.querySelector("#map-view");
const resultsMap = document.querySelector("#results-map");
const accountButton = document.querySelector("#account-button");
const accountDialog = document.querySelector("#account-dialog");
const accountForm = document.querySelector("#account-form");
const loginTab = document.querySelector("#login-tab");
const registerTab = document.querySelector("#register-tab");
const nameField = document.querySelector("#name-field");
const accountName = document.querySelector("#account-name");
const accountEmail = document.querySelector("#account-email");
const accountPassword = document.querySelector("#account-password");
const accountSubmit = document.querySelector("#account-submit");
const accountMessage = document.querySelector("#account-message");
const signedOutPanel = document.querySelector("#signed-out-panel");
const signedInPanel = document.querySelector("#signed-in-panel");
const accountGreeting = document.querySelector("#account-greeting");
const ratingSummary = document.querySelector("#rating-summary");
const wrappedStats = document.querySelector("#wrapped-stats");
const PAGE_SIZE = 5;
let activeQuery = "";
let displayedCount = 0;
let visibleRecommendations = [];
let venueMap = null;
let venueMarkerLayer = null;
let currentAccount = null;
let userRatings = new Map();
let accountMode = "login";

const existingSession = sessionStorage.getItem("recommendation-session");
const sessionId = existingSession || crypto.randomUUID();
sessionStorage.setItem("recommendation-session", sessionId);

async function recordFeedback(venueId, eventType, query, recommendationRank, context) {
  const response = await fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      venueId,
      eventType,
      query,
      recommendationRank,
      sessionId,
      requestId: context.requestId,
      rankingVersion: context.rankingVersion,
      datasetVersion: context.datasetVersion,
    }),
  });
  if (!response.ok) throw new Error("Feedback could not be recorded");
  return response.json();
}

async function accountRequest(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Account request failed");
  }
  return response.status === 204 ? null : response.json();
}

async function loadAccount() {
  try {
    currentAccount = await accountRequest("/account/me");
    const ratings = await accountRequest("/account/ratings");
    userRatings = new Map(ratings.map((rating) => [rating.venueId, rating]));
    const stats = await accountRequest("/account/stats");
    document.querySelector("#week-stat").textContent = stats.visitedThisWeek;
    document.querySelector("#london-stat").textContent = `${stats.londonPercent}%`;
    document.querySelector("#rating-stat").textContent = stats.averageRating ?? "—";
    document.querySelector("#wrapped-message").textContent = stats.visitedTotal
      ? `You have logged ${stats.visitedTotal} pub${stats.visitedTotal === 1 ? "" : "s"} so far.`
      : "Rate your first pub to start your story.";
  } catch {
    currentAccount = null;
    userRatings.clear();
  }
  renderAccountState();
}

function renderAccountState() {
  accountButton.textContent = currentAccount ? currentAccount.displayName : "Sign in";
  signedOutPanel.hidden = Boolean(currentAccount);
  signedInPanel.hidden = !currentAccount;
  wrappedStats.hidden = !currentAccount;
  if (currentAccount) {
    document.querySelector("#page-title").textContent = `Hello, ${currentAccount.displayName}`;
    accountGreeting.textContent = `Hello, ${currentAccount.displayName}`;
    const rated = [...userRatings.values()].filter((item) => item.rating).length;
    const visited = [...userRatings.values()].filter((item) => item.beenHere).length;
    ratingSummary.textContent = `${visited} visited · ${rated} rated. Your ratings now shape this feed.`;
  }
  if (!currentAccount) document.querySelector("#page-title").textContent = "Discover pubs";
}

function setAccountMode(mode) {
  accountMode = mode;
  const registering = mode === "register";
  loginTab.setAttribute("aria-pressed", String(!registering));
  registerTab.setAttribute("aria-pressed", String(registering));
  nameField.hidden = !registering;
  accountName.required = registering;
  accountPassword.autocomplete = registering ? "new-password" : "current-password";
  accountSubmit.textContent = registering ? "Create account" : "Sign in";
  accountMessage.textContent = "";
}

async function saveRating(venueId, rating) {
  if (!currentAccount) {
    accountDialog.showModal();
    throw new Error("Sign in to keep a pub diary")
  }
  const saved = await accountRequest(`/account/ratings/${encodeURIComponent(venueId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ beenHere: true, rating }),
  });
  userRatings.set(venueId, saved);
  renderAccountState();
  return saved;
}

function safeWebsite(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function makeTextElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function buildVenueCard(venue, rank, query, context) {
  const card = document.createElement("article");
  card.className = "venue-card";
  card.id = `venue-${venue.venueId}`;
  card.style.animationDelay = `${Math.min(rank * 45, 225)}ms`;

  const cardTop = document.createElement("div");
  cardTop.className = "card-top";
  const rankBadge = makeTextElement("span", "rank", String(rank));
  const titleBlock = document.createElement("div");
  titleBlock.className = "venue-heading";
  titleBlock.append(makeTextElement("h3", "", venue.name));
  const addressParts = [venue.address, venue.postcode].filter(Boolean);
  titleBlock.append(makeTextElement("p", "address", addressParts.join(", ") || "Address unavailable"));
  cardTop.append(rankBadge, titleBlock);
  const cardBadges = document.createElement("div");
  cardBadges.className = "card-badges";
  cardBadges.append(makeTextElement("span", "score", `${Math.round(venue.score * 100)}% match`));
  if (venue.priceLevel) {
    const priceSymbols = { cheap: "£", moderate: "££", expensive: "£££" };
    const priceText = venue.guinnessPriceGbp
      ? `${priceSymbols[venue.priceLevel]} Guinness £${venue.guinnessPriceGbp.toFixed(2)}`
      : `${priceSymbols[venue.priceLevel]} ${venue.priceLevel}`;
    const priceBadge = makeTextElement("span", "price-badge", priceText);
    if (venue.priceEvidenceUrl) {
      const isCommunitySource = new URL(venue.priceEvidenceUrl).hostname.endsWith("pint-prices.com");
      const priceLink = document.createElement("a");
      priceLink.href = venue.priceEvidenceUrl;
      priceLink.target = "_blank";
      priceLink.rel = "noopener noreferrer";
      priceLink.title = isCommunitySource
        ? "Community-reported price · open evidence"
        : "Official-menu price · open evidence";
      priceLink.append(priceBadge);
      cardBadges.append(priceLink);
    } else {
      cardBadges.append(priceBadge);
    }
  }
  cardTop.append(cardBadges);
  card.append(cardTop);

  if (venue.verifiedFeatures?.length) {
    const facts = document.createElement("ul");
    facts.className = "reasons";
    for (const fact of venue.verifiedFeatures) facts.append(makeTextElement("li", "", fact));
    card.append(facts);
  }

  if (venue.unknownFeatures?.length) {
    const unknown = document.createElement("details");
    unknown.className = "unknown-panel";
    unknown.append(makeTextElement("summary", "", `${venue.unknownFeatures.length} details unknown`));
    unknown.append(
      makeTextElement(
        "p",
        "unknown-copy",
        `Not yet verified: ${venue.unknownFeatures.join(", ")}. Unknown does not mean no.`,
      ),
    );
    card.append(unknown);
  }

  if (venue.evidence?.length) {
    const evidencePanel = document.createElement("details");
    evidencePanel.className = "evidence-panel";
    evidencePanel.append(makeTextElement("summary", "", `Verified from ${venue.evidence.length} source detail${venue.evidence.length === 1 ? "" : "s"}`));
    const evidence = document.createElement("ul");
    evidence.className = "evidence-list";
    for (const fact of venue.evidence) evidence.append(makeTextElement("li", "", fact));
    evidencePanel.append(evidence);

    const sourceLinks = document.createElement("div");
    sourceLinks.className = "source-links";
    venue.evidenceSourceUrls.forEach((source, index) => {
      const url = safeWebsite(source);
      if (!url) return;
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `Source ${index + 1}`;
      sourceLinks.append(link);
    });
    evidencePanel.append(sourceLinks);
    card.append(evidencePanel);
  }

  const links = document.createElement("div");
  links.className = "card-links";
  const website = safeWebsite(venue.website);
  if (website) {
    const websiteLink = document.createElement("a");
    websiteLink.href = website;
    websiteLink.target = "_blank";
    websiteLink.rel = "noopener noreferrer";
    websiteLink.textContent = "Website ↗";
    websiteLink.addEventListener("click", () => {
      void recordFeedback(venue.venueId, "click", query, rank, context);
    });
    links.append(websiteLink);
  }
  if (venue.phone) {
    const phoneLink = document.createElement("a");
    phoneLink.href = `tel:${venue.phone.replace(/[^+\d]/g, "")}`;
    phoneLink.textContent = "Call";
    phoneLink.addEventListener("click", () => {
      void recordFeedback(venue.venueId, "click", query, rank, context);
    });
    links.append(phoneLink);
  }
  card.append(links);

  const diary = document.createElement("div");
  diary.className = "diary-row";
  const currentRating = venue.userRating || userRatings.get(venue.venueId)?.rating || 0;
  const diaryCopy = document.createElement("div");
  diaryCopy.append(makeTextElement("strong", "", "Been here?"));
  diaryCopy.append(
    makeTextElement(
      "span",
      "",
      currentRating
        ? `${currentRating}/5 · saved to your diary`
        : "Tap a star to add it to your pub diary",
    ),
  );
  const stars = document.createElement("div");
  stars.className = "star-rating";
  stars.setAttribute("aria-label", `Rate ${venue.name}`);
  for (let value = 1; value <= 5; value += 1) {
    const star = document.createElement("button");
    star.type = "button";
    star.textContent = value <= currentRating ? "★" : "☆";
    star.setAttribute("aria-label", `${value} star${value === 1 ? "" : "s"}`);
    star.setAttribute("aria-pressed", String(value === currentRating));
    star.addEventListener("click", async () => {
      try {
        const saved = await saveRating(venue.venueId, value);
        [...stars.children].forEach((item, index) => {
          item.textContent = index < saved.rating ? "★" : "☆";
          item.setAttribute("aria-pressed", String(index + 1 === saved.rating));
        });
        diaryCopy.querySelector("span").textContent = `${saved.rating}/5 · saved to your diary`;
      } catch (error) {
        diaryCopy.querySelector("span").textContent = error.message;
      }
    });
    stars.append(star);
  }
  diary.append(diaryCopy, stars);
  card.append(diary);

  if (venue.personalReason) {
    card.append(makeTextElement("p", "personal-reason", `✦ ${venue.personalReason}`));
  }

  const feedbackActions = document.createElement("div");
  feedbackActions.className = "feedback-actions";
  const feedbackMessage = makeTextElement("p", "feedback-message", "");
  for (const [eventType, label] of [["save", "♡ Save"], ["like", "＋ More like this"], ["dislike", "− Less like this"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", async () => {
      feedbackMessage.classList.remove("error");
      feedbackMessage.textContent = "Saving…";
      try {
        await recordFeedback(venue.venueId, eventType, query, rank, context);
        for (const sibling of feedbackActions.querySelectorAll("button")) {
          sibling.setAttribute("aria-pressed", String(sibling === button));
        }
        feedbackMessage.textContent = "Thanks—feedback saved locally.";
      } catch {
        feedbackMessage.classList.add("error");
        feedbackMessage.textContent = "Could not save feedback. Please try again.";
      }
    });
    feedbackActions.append(button);
  }
  card.append(feedbackActions, feedbackMessage);
  return card;
}

function initialiseMap() {
  if (venueMap) return true;
  if (!window.L) {
    resultsMap.textContent = "The geographic map could not load. Check your internet connection.";
    resultsMap.classList.add("map-error");
    return false;
  }

  // Leaflet supplies the interaction; OpenStreetMap supplies the visible geography.
  venueMap = L.map(resultsMap, { scrollWheelZoom: false }).setView([51.515, -0.145], 13);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(venueMap);
  venueMarkerLayer = L.featureGroup().addTo(venueMap);
  return true;
}

function buildMapPopup(venue) {
  // Build popup content as DOM nodes so venue data is never inserted as HTML.
  const popup = document.createElement("div");
  popup.className = "map-popup";
  popup.append(makeTextElement("strong", "", venue.name));
  const address = [venue.address, venue.postcode].filter(Boolean).join(", ");
  popup.append(makeTextElement("span", "", address || "Address unavailable"));

  const listButton = document.createElement("button");
  listButton.type = "button";
  listButton.textContent = "Show in list";
  listButton.addEventListener("click", () => {
    selectView("list");
    document.querySelector(`#venue-${CSS.escape(venue.venueId)}`)?.scrollIntoView({ behavior: "smooth" });
  });
  popup.append(listButton);
  return popup;
}

function renderMap() {
  if (!visibleRecommendations.length) {
    venueMarkerLayer?.clearLayers();
    return;
  }
  if (!initialiseMap()) return;
  venueMarkerLayer.clearLayers();

  visibleRecommendations.forEach((venue, index) => {
    // Numbered markers match the rank displayed in the result cards.
    const icon = L.divIcon({
      className: "ranked-map-marker",
      html: `<span>${index + 1}</span>`,
      iconSize: [34, 34],
      iconAnchor: [17, 17],
      popupAnchor: [0, -18],
    });
    const marker = L.marker([venue.latitude, venue.longitude], {
      icon,
      title: `${index + 1}. ${venue.name}`,
    });
    marker.bindPopup(buildMapPopup(venue));
    venueMarkerLayer.addLayer(marker);
  });

  // Leaflet needs a size refresh because the map starts inside a hidden view.
  venueMap.invalidateSize();
  const bounds = venueMarkerLayer.getBounds();
  if (bounds.isValid()) venueMap.fitBounds(bounds.pad(0.18), { maxZoom: 15 });
}

function selectView(view) {
  const showMap = view === "map";
  resultsContainer.hidden = showMap;
  resultsMap.hidden = !showMap;
  listViewButton.setAttribute("aria-pressed", String(!showMap));
  mapViewButton.setAttribute("aria-pressed", String(showMap));
  if (showMap) renderMap();
}

async function search(query, append = false) {
  const offset = append ? displayedCount : 0;
  searchButton.disabled = true;
  loadMoreButton.disabled = true;
  statusElement.classList.remove("error");
  statusElement.textContent = append ? "Loading more matching pubs…" : "Finding matching pubs…";
    if (!append) {
    activeQuery = query;
    displayedCount = 0;
    summaryElement.textContent = "";
    resultsContainer.replaceChildren();
    activeFilters.replaceChildren();
    loadMoreButton.hidden = true;
    }

  try {
    const response = await fetch("/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: PAGE_SIZE, offset }),
    });
    if (!response.ok) throw new Error("Search request failed");
    const payload = await response.json();
    const recommendations = payload.recommendations;
    const context = {
      requestId: payload.requestId,
      rankingVersion: payload.rankingVersion,
      datasetVersion: payload.datasetVersion,
    };
    displayedCount = offset + recommendations.length;
    resultsTitle.textContent = payload.totalAvailable ? "Your feed" : "No matches yet";
    summaryElement.textContent = payload.totalAvailable
      ? `${displayedCount} of ${payload.totalAvailable} results`
      : "0 results";
    statusElement.textContent = payload.totalAvailable
      ? payload.personalised
        ? "Personalised from verified venue facts and pubs you rated highly."
        : payload.retrievalMode === "hybrid_rag"
        ? "Ranked from local search and verified venue evidence."
        : "Ranked from the venue facts currently available."
      : "Try removing a feature. Unknown venue details are not treated as confirmed matches.";
    // Show exactly which structured constraints reached filtering and ranking.
    activeFilters.replaceChildren(
      ...payload.activePreferences.map((preference) =>
        makeTextElement("span", "filter-chip", preference.replaceAll(":", ": ")),
      ),
    );
    const diagnostics = [...payload.noResultReasons, ...payload.warnings];
    if (diagnostics.length) statusElement.textContent += ` ${diagnostics.join(" ")}`;

    recommendations.forEach((venue, index) => {
      const rank = offset + index + 1;
      resultsContainer.append(buildVenueCard(venue, rank, query, context));
      void recordFeedback(venue.venueId, "impression", query, rank, context);
    });
    visibleRecommendations = append
      ? [...visibleRecommendations, ...recommendations]
      : recommendations;
    if (!resultsMap.hidden) renderMap();
    loadMoreButton.hidden = !payload.hasMore;
  } catch {
    if (!append) resultsTitle.textContent = "Search unavailable";
    statusElement.classList.add("error");
    statusElement.textContent = "The search could not be completed. Check that the API is running.";
  } finally {
    searchButton.disabled = false;
    loadMoreButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (query) void search(query);
});

for (const suggestion of document.querySelectorAll("[data-query]")) {
  suggestion.addEventListener("click", () => {
    queryInput.value = suggestion.dataset.query;
    void search(suggestion.dataset.query);
  });
}

loadMoreButton.addEventListener("click", () => {
  if (activeQuery) void search(activeQuery, true);
});

applyFiltersButton.addEventListener("click", () => {
  const selectedFeatures = [...document.querySelectorAll(".feature-controls input:checked")]
    .map((input) => input.value);
  const parts = [areaFilter.value ? `pub in ${areaFilter.value}` : "pub"];
  if (selectedFeatures.length) parts.push(`with ${selectedFeatures.join(" and ")}`);
  const query = parts.join(" ");
  queryInput.value = query;
  void search(query);
});

listViewButton.addEventListener("click", () => selectView("list"));
mapViewButton.addEventListener("click", () => selectView("map"));

accountButton.addEventListener("click", () => accountDialog.showModal());
document.querySelector("#close-account").addEventListener("click", () => accountDialog.close());
loginTab.addEventListener("click", () => setAccountMode("login"));
registerTab.addEventListener("click", () => setAccountMode("register"));
document.querySelector("#logout-button").addEventListener("click", async () => {
  await accountRequest("/account/logout", { method: "POST" });
  currentAccount = null;
  userRatings.clear();
  renderAccountState();
  accountDialog.close();
  void search(activeQuery || queryInput.value.trim());
});
accountForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  accountMessage.textContent = "Saving…";
  const body = { email: accountEmail.value, password: accountPassword.value };
  if (accountMode === "register") body.displayName = accountName.value;
  try {
    currentAccount = await accountRequest(`/account/${accountMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await loadAccount();
    accountForm.reset();
    accountDialog.close();
    void search(activeQuery || queryInput.value.trim());
  } catch (error) {
    accountMessage.textContent = error.message;
  }
});

// A discovery feed should have something useful in it as soon as the app opens.
void loadAccount().then(() => search(queryInput.value.trim()));
