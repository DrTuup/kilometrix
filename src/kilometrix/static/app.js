// Kilometrix UI. Plain JS, no build step. Server already returns times in DISPLAY_TIMEZONE.
"use strict";

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const fmtKm = (n) => Number(n).toFixed(1);

function fmtTime(iso) {
  if (!iso) return "";
  return iso.slice(0, 16).replace("T", " ");
}

function locationCell(place, lat, lon, at) {
  if (lat == null || lon == null) return '<span class="muted">no location</span>';
  const stamp = at ? `<br><small class="muted">${esc(fmtTime(at))}</small>` : "";
  const coords = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  const mapUrl = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`;
  const label = place ? `<span>${esc(place)}</span>` : "";
  return `${label}<br><a class="coords muted" href="${mapUrl}" target="_blank" rel="noopener">${esc(coords)}</a>${stamp}`;
}

function filterQuery() {
  const params = new URLSearchParams();
  if ($("from").value) params.set("from", $("from").value);
  if ($("to").value) params.set("to", $("to").value);
  if ($("tag").value.trim()) params.set("tag", $("tag").value.trim());
  return params;
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

// Render a list of {label, km} as bars scaled to the largest value.
function bars(rows, emptyText) {
  if (!rows.length) return `<li class="empty">${esc(emptyText)}</li>`;
  const max = Math.max(...rows.map((r) => r.km), 1);
  return rows.map((r) => `
    <li>
      <div class="bar-row"><span>${esc(r.label)}</span><b>${fmtKm(r.km)} km</b></div>
      <div class="bar"><span style="width:${((r.km / max) * 100).toFixed(1)}%"></span></div>
    </li>`).join("");
}

async function loadTotals(params) {
  const p = new URLSearchParams(params);
  p.set("period", $("period").value);
  const t = await getJson(`/totals?${p}`);
  $("kpi-total").textContent = fmtKm(t.total_km);
  $("kpi-untagged").textContent = fmtKm(t.untagged_km);

  const byTag = Object.entries(t.by_tag)
    .map(([label, km]) => ({ label, km }))
    .sort((a, b) => b.km - a.km);
  const byPeriod = Object.entries(t.by_period)
    .map(([label, km]) => ({ label, km }));
  $("by-tag").innerHTML = bars(byTag, "no tagged trips yet");
  $("by-period").innerHTML = bars(byPeriod, "no trips in this range");
}

function tagChips(tags) {
  if (!tags.length) return '<span class="muted">—</span>';
  return tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("");
}

async function loadTrips(params) {
  const trips = await getJson(`/trips?${params}`);
  $("kpi-trips").textContent = trips.length;
  $("trips").innerHTML = trips.map((t) => `
    <tr>
      <td>${esc(fmtTime(t.started_at))}</td>
      <td>${t.ended_at
        ? esc(fmtTime(t.ended_at))
        : '<span class="live">in progress</span>'}</td>
      <td class="num km">${fmtKm(t.distance_km)}</td>
      <td>${locationCell(t.start_place, t.start_lat, t.start_lon, null)}</td>
      <td>${locationCell(t.end_place, t.end_lat, t.end_lon, t.position_at)}</td>
      <td>
        <div class="tag-edit">
          ${tagChips(t.tags)}
          <input type="text" data-trip="${t.id}" value="${esc(t.tags.join(", "))}" placeholder="add tags">
          <button class="btn" data-save="${t.id}">Save</button>
        </div>
      </td>
    </tr>`).join("") || '<tr><td colspan="6" class="empty">no trips match these filters</td></tr>';
}

async function loadTagList() {
  const tags = await getJson("/tags");
  $("tag-list").innerHTML = tags.map((t) => `<option value="${esc(t)}">`).join("");
}

async function refresh() {
  const params = filterQuery();
  try {
    await Promise.all([loadTotals(params), loadTrips(params), loadTagList()]);
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="error">${esc(err.message)}</p>`);
  }
}

async function saveTags(id) {
  const input = document.querySelector(`input[data-trip="${id}"]`);
  const tags = input.value.split(",").map((s) => s.trim()).filter(Boolean);
  const res = await fetch(`/trips/${id}/tags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert(`Could not save tags: ${body.detail ?? res.status}`);
    return;
  }
  await refresh();
}

$("apply").addEventListener("click", refresh);
$("period").addEventListener("change", refresh);
$("trips").addEventListener("click", (e) => {
  const id = e.target.dataset.save;
  if (id) saveTags(id);
});
refresh();
