const statusElement = document.querySelector("#diary-status");
const grid = document.querySelector("#diary-grid");
const statsPanel = document.querySelector("#diary-stats");
const entryDialog = document.querySelector("#entry-dialog");
const entryForm = document.querySelector("#entry-form");
const entryRating = document.querySelector("#entry-rating");
const entryNote = document.querySelector("#entry-note");
const entryPhoto = document.querySelector("#entry-photo");
const entryMessage = document.querySelector("#entry-message");
let activeEntry = null;

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401) {
    window.location.href = "/";
    return null;
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Request failed");
  }
  return response.json();
}

function text(tag, className, value) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = value;
  return element;
}

function renderStats(stats) {
  const values = [
    [stats.visitedTotal, "pubs visited"],
    [stats.visitedThisWeek, "this week"],
    [`${stats.londonPercent}%`, "of London"],
    [stats.averageRating ?? "—", "average rating"],
  ];
  statsPanel.replaceChildren(...values.map(([value, label]) => {
    const item = document.createElement("div");
    item.append(text("strong", "", value), text("span", "", label));
    return item;
  }));
  statsPanel.hidden = false;
}

function renderEntry(entry) {
  const card = document.createElement("article");
  card.className = "diary-card";
  if (entry.photoUrl) {
    const image = document.createElement("img");
    image.src = entry.photoUrl;
    image.alt = `Private memory from ${entry.name}`;
    card.append(image);
  }
  const copy = document.createElement("div");
  copy.className = "diary-card-copy";
  const date = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(new Date(entry.visitedAt));
  copy.append(text("p", "eyebrow", `${entry.area} · ${date}`));
  copy.append(text("h2", "", entry.name));
  copy.append(text("p", "diary-stars", entry.rating ? "★".repeat(entry.rating) + "☆".repeat(5 - entry.rating) : "Visited · not rated"));
  copy.append(text("p", "diary-note", entry.privateNote || "No private note yet."));
  const edit = text("button", "secondary-button", "Edit memory");
  edit.type = "button";
  edit.addEventListener("click", () => openEditor(entry));
  copy.append(edit);
  card.append(copy);
  return card;
}

function openEditor(entry) {
  activeEntry = entry;
  document.querySelector("#entry-title").textContent = entry.name;
  entryRating.value = entry.rating || "";
  entryNote.value = entry.privateNote || "";
  entryPhoto.value = "";
  entryMessage.textContent = "";
  entryDialog.showModal();
}

function readPhoto(file) {
  if (!file) return Promise.resolve(null);
  if (file.size > 5_000_000) return Promise.reject(new Error("Photo must be smaller than 5 MB"));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Photo could not be read"));
    reader.readAsDataURL(file);
  });
}

async function loadDiary() {
  try {
    const [entries, stats] = await Promise.all([request("/account/diary"), request("/account/stats")]);
    if (!entries || !stats) return;
    renderStats(stats);
    grid.replaceChildren(...entries.map(renderEntry));
    statusElement.textContent = entries.length
      ? `${entries.length} private ${entries.length === 1 ? "memory" : "memories"}, newest first.`
      : "Your diary is empty. Rate a pub from the Explore feed to begin.";
  } catch (error) {
    statusElement.classList.add("error");
    statusElement.textContent = error.message;
  }
}

entryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  entryMessage.textContent = "Saving…";
  try {
    const photoDataUrl = await readPhoto(entryPhoto.files[0]);
    await request(`/account/ratings/${encodeURIComponent(activeEntry.venueId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        beenHere: true,
        rating: entryRating.value ? Number(entryRating.value) : null,
        privateNote: entryNote.value,
        photoDataUrl,
      }),
    });
    entryDialog.close();
    await loadDiary();
  } catch (error) {
    entryMessage.textContent = error.message;
  }
});

document.querySelector("#close-entry").addEventListener("click", () => entryDialog.close());
void loadDiary();
