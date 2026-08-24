const queue = document.querySelector("#review-queue");
const reviewStatus = document.querySelector("#review-status");
const priceForm = document.querySelector("#missing-price-form");
const priceVenue = document.querySelector("#price-venue");
const priceFormStatus = document.querySelector("#price-form-status");

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

async function decide(reviewId, reviewStatusValue, card) {
  const response = await fetch(`/review/features/${reviewId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reviewStatus: reviewStatusValue,
      reviewReason: reviewStatusValue === "approved"
        ? "Official evidence explicitly supports this fact."
        : "Official evidence does not explicitly support this fact.",
    }),
  });
  if (!response.ok) throw new Error("Decision could not be saved");
  card.remove();
  reviewStatus.textContent = queue.children.length
    ? `${queue.children.length} facts remaining on this page.`
    : "This review page is complete.";
}

function renderItem(item) {
  const card = document.createElement("article");
  card.className = "review-card";
  card.append(textElement("h2", "", item.venueName));
  card.append(textElement("p", "review-claim", `${item.feature}: ${item.suggestedValue ? "yes" : "no"}`));
  card.append(textElement("p", "review-evidence", item.evidenceText));
  const source = document.createElement("a");
  source.href = item.evidenceUrl;
  source.target = "_blank";
  source.rel = "noopener noreferrer";
  source.textContent = "Open official evidence";
  card.append(source);

  const actions = document.createElement("div");
  actions.className = "review-actions";
  for (const [status, label] of [["approved", "Approve"], ["rejected", "Reject"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await decide(item.reviewId, status, card);
      } catch {
        button.disabled = false;
        reviewStatus.textContent = "The decision could not be saved.";
      }
    });
    actions.append(button);
  }
  card.append(actions);
  return card;
}

async function loadQueue() {
  try {
    const response = await fetch("/review/features?limit=50");
    if (!response.ok) throw new Error("Queue unavailable");
    const items = await response.json();
    queue.replaceChildren(...items.map(renderItem));
    reviewStatus.textContent = items.length
      ? `${items.length} official-site claims await review.`
      : "No official-site claims await review.";
  } catch {
    reviewStatus.textContent = "The review queue could not be loaded.";
  }
}

void loadQueue();

async function loadMissingPriceVenues() {
  try {
    const response = await fetch("/review/prices/missing");
    if (!response.ok) throw new Error("Missing prices unavailable");
    const venues = await response.json();
    const options = venues.map((venue) => {
      const option = document.createElement("option");
      option.value = venue.venueId;
      option.textContent = `${venue.name} · ${venue.area}`;
      return option;
    });
    priceVenue.replaceChildren(new Option("Choose a pub", ""), ...options);
    priceFormStatus.textContent = venues.length
      ? `${venues.length} pubs still need a verified price.`
      : "Every pub has a verified price.";
    priceForm.querySelector("button").disabled = !venues.length;
    return venues.length;
  } catch {
    priceFormStatus.textContent = "Missing-price pubs could not be loaded.";
    return null;
  }
}

priceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = priceForm.querySelector("button");
  button.disabled = true;
  priceFormStatus.textContent = "Saving…";
  try {
    const response = await fetch("/review/prices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        venueId: priceVenue.value,
        observedPriceGbp: Number(document.querySelector("#price-gbp").value),
        evidenceUrl: document.querySelector("#price-url").value,
      }),
    });
    if (!response.ok) throw new Error("Price could not be saved");
    const observation = await response.json();
    priceForm.reset();
    const remaining = await loadMissingPriceVenues();
    const remainingText = remaining === null ? "" : ` ${remaining} pubs remain.`;
    priceFormStatus.textContent = `Saved ${observation.venueName} at £${observation.observedPriceGbp.toFixed(2)} (${observation.priceLevel}).${remainingText}`;
  } catch {
    button.disabled = false;
    priceFormStatus.textContent = "The price could not be saved. Check the amount and evidence URL.";
  }
});

void loadMissingPriceVenues();
