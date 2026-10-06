const $ = (id) => document.getElementById(id),
  video = $("video"),
  overlay = $("overlay");
const palette = ["#58adff", "#38d7a1", "#ffb363", "#cb91ff", "#ff7e9d"];
const color = (id) => palette[(id - 1) % palette.length];
let result = null,
  selected = null,
  mode = "original",
  busy = false,
  allRecordings = [];
const fmt = (t) =>
  `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
function notify(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return response.json();
}
function setBusy(value) {
  busy = value;
  ["sampleBtn", "emptySample", "uploadBtn", "submitUpload"].forEach(
    (id) => ($(id).disabled = value),
  );
  $("recordingSelect").disabled = value;
  document.querySelectorAll(".recording").forEach((b) => (b.disabled = value));
}
async function watch(job) {
  setBusy(true);
  try {
    while (true) {
      const status = await api(`/api/jobs/${job.id}`);
      notify(
        `Processing recording · ${status.progress || 0}% · ${status.status}`,
      );
      if (status.status === "error") throw new Error(status.error);
      if (status.status === "ready") break;
      await new Promise((r) => setTimeout(r, 600));
    }
    await load(job.id);
  } catch (e) {
    notify(e.message, true);
  } finally {
    setBusy(false);
  }
}
async function load(identity) {
  result = await api(`/api/jobs/${identity}/result`);
  selected = result.events[0]?.id ?? null;
  resetFilters();
  setMode("original");
  render();
  await refreshRecordings();
  localStorage.setItem("synopsis-recording", identity);
  notify(
    result.synthetic
      ? "Generated test footage · All observations and output videos are calculated from this scene."
      : `Analysis ready · ${result.detector === "motion" ? "Foreground motion candidates" : "Local model detections"} · Verify each event against source footage.`,
  );
}
async function refreshRecordings() {
  allRecordings = await api("/api/recordings");
  const list = $("recordings"),
    select = $("recordingSelect");
  list.replaceChildren();
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Select a recording";
  select.append(empty);
  if (!allRecordings.length)
    list.innerHTML =
      '<p class="empty-text">Your analyzed clips will appear here.</p>';
  for (const r of allRecordings) {
    const b = document.createElement("button");
    b.className = "recording" + (r.id === result?.id ? " active" : "");
    const title = document.createElement("b");
    title.textContent = r.camera;
    const subtitle = document.createElement("small");
    subtitle.textContent =
      fmt(r.duration) +
      (r.synthetic ? " · Generated sample" : " · Uploaded recording");
    b.append(title, subtitle);
    b.disabled = busy;
    b.onclick = () => load(r.id).catch((e) => notify(e.message, true));
    list.append(b);
    const option = document.createElement("option");
    option.value = r.id;
    option.textContent = r.camera + " · " + fmt(r.duration);
    select.append(option);
  }
  select.value = result?.id || "";
}
$("recordingSelect").onchange = (e) => {
  if (e.target.value)
    load(e.target.value).catch((err) => notify(err.message, true));
};
async function sample() {
  setBusy(true);
  try {
    await watch(await api("/api/sample", { method: "POST" }));
  } catch (e) {
    notify(e.message, true);
    setBusy(false);
  }
}
$("sampleBtn").onclick = sample;
$("emptySample").onclick = sample;
$("uploadBtn").onclick = () => $("uploadDialog").showModal();
$("cancelUpload").onclick = () => $("uploadDialog").close();
$("uploadForm").onsubmit = async (e) => {
  e.preventDefault();
  const file = $("uploadFile").files[0];
  if (!file) return;
  if (file.size > 200 * 1024 * 1024) {
    notify("Choose a recording smaller than 200 MB.", true);
    return;
  }
  const form = new FormData();
  form.append("file", file);
  form.append("camera", $("cameraName").value);
  form.append(
    "started_at",
    $("startedAt").value ? $("startedAt").value + "Z" : "",
  );
  setBusy(true);
  $("uploadDialog").close();
  try {
    await watch(await api("/api/upload", { method: "POST", body: form }));
  } catch (err) {
    notify(err.message, true);
    setBusy(false);
  }
};
function resetFilters() {
  $("search").value = "";
  $("category").value = "all";
  $("fromTime").value = "0";
  $("toTime").value = "";
}
$("resetFilters").onclick = () => {
  resetFilters();
  renderEvents();
};
function filtered() {
  if (!result) return [];
  const query = $("search").value.toLowerCase().trim(),
    category = $("category").value,
    a = Math.max(0, Number($("fromTime").value) || 0),
    b = $("toTime").value === "" ? Infinity : Number($("toTime").value);
  return result.events.filter(
    (e) =>
      e.end >= a &&
      e.start <= b &&
      (category === "all" ||
        (category === "long" ? e.long_activity : e.category === category)) &&
      [e.label, e.note, ...e.tags, "track " + e.id]
        .join(" ")
        .toLowerCase()
        .includes(query),
  );
}
for (const id of ["search", "category", "fromTime", "toTime"])
  $(id).addEventListener("input", renderEvents);
function setMode(next, seek = 0) {
  mode = next;
  document.querySelectorAll("[data-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
    b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
  });
  $("modeBanner").hidden = mode !== "synopsis" || !result;
  $("boxes").disabled = mode !== "original";
  if (result) {
    video.onloadedmetadata = () => {
      video.currentTime = Math.min(Math.max(0, seek), video.duration || 0);
    };
    video.src = `/api/jobs/${result.id}/media/${mode}.mp4`;
    video.load();
    $("emptyVideo").hidden = true;
  }
  drawTimeline();
}
function jump(time) {
  setMode("original", time);
  $("videoPanel").scrollIntoView({ behavior: "smooth", block: "start" });
}
document.querySelectorAll("[data-mode]").forEach(
  (b) =>
    (b.onclick = () => {
      if (result) setMode(b.dataset.mode);
    }),
);
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      document
        .querySelectorAll("[data-view]")
        .forEach((x) => x.classList.toggle("active", x === b));
      const view = b.dataset.view;
      if (view === "search") {
        $("search").focus();
        return;
      }
      if (view === "synopsis" && result) setMode("synopsis");
      $(
        view === "events"
          ? "momentsPanel"
          : view === "reports"
            ? "exportPanel"
            : "videoPanel",
      ).scrollIntoView({ behavior: "smooth", block: "start" });
    }),
);
function render() {
  $("cameraTitle").textContent = result?.camera || "Recording workspace";
  $("sourceBadge").textContent = result
    ? result.synthetic
      ? "GENERATED SAMPLE"
      : "UPLOADED RECORDING"
    : "NO VIDEO LOADED";
  $("sourceDuration").textContent = fmt(result?.duration || 0);
  $("synopsisDuration").textContent = fmt(result?.synopsis_duration || 0);
  $("reduction").textContent = result
    ? Math.round((1 - result.synopsis_duration / result.duration) * 100) + "%"
    : "N/A";
  $("eventCount").textContent = result?.events.length || 0;
  $("eventsBadge").textContent = (result?.events.length || 0) + " TRACKS";
  $("detectorLabel").textContent =
    result?.detector === "model"
      ? "Local object model"
      : "Foreground motion candidates";
  const facts = [
    [
      "Detector",
      result?.detector === "model" ? "Local object model" : "Foreground motion",
    ],
    [
      "Analysis rate",
      result ? result.sample_rate.toFixed(2) + " fps" : "Up to 5 fps",
    ],
    [
      "Long activity",
      result ? result.dwell_seconds + "+ seconds" : "15+ seconds",
    ],
    ["Omitted tracks", String(result?.omitted_tracks || 0)],
  ];
  $("processingFacts").replaceChildren();
  for (const [k, v] of facts) {
    const dt = document.createElement("dt"),
      dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = v;
    $("processingFacts").append(dt, dd);
  }
  renderEvents();
  renderDetails();
  drawTimeline();
  for (const [id, path] of [
    ["synopsisDownload", "media/synopsis.mp4?download=true"],
    ["reelDownload", "media/reel.mp4?download=true"],
    ["csvDownload", "export?format=csv"],
    ["jsonDownload", "export?format=json"],
  ]) {
    const a = $(id);
    a.setAttribute("aria-disabled", String(!result));
    if (result) a.href = `/api/jobs/${result.id}/${path}`;
    else a.removeAttribute("href");
  }
}
function choose(event) {
  selected = event.id;
  renderEvents();
  renderDetails();
  jump(event.start);
}
function renderEvents() {
  const visible = filtered();
  $("filterCount").textContent = visible.length + " events";
  const moments = $("moments"),
    events = $("events");
  moments.replaceChildren();
  events.replaceChildren();
  if (!visible.length) {
    for (const root of [moments, events]) {
      const p = document.createElement("p");
      p.className = "empty-text";
      p.textContent = result
        ? "No observations match these filters."
        : "Analyze a clip to explore its activity.";
      root.append(p);
    }
    drawTimeline();
    return;
  }
  for (const e of visible) {
    const imageUrl = `/api/jobs/${result.id}/media/${e.thumbnail}`;
    const moment = document.createElement("button");
    moment.className = "moment" + (selected === e.id ? " selected" : "");
    moment.setAttribute("aria-pressed", String(selected === e.id));
    const img = document.createElement("img");
    img.src = imageUrl;
    img.alt = e.label + " observation";
    const content = document.createElement("span");
    content.className = "moment-content";
    const time = document.createElement("span");
    time.textContent = fmt(e.start);
    time.style.color = color(e.id);
    const title = document.createElement("strong");
    title.textContent = e.label;
    const subtitle = document.createElement("small");
    subtitle.textContent =
      "Track #" + e.id + " · " + e.duration.toFixed(1) + "s";
    content.append(time, title, subtitle);
    moment.append(img, content);
    moment.onclick = () => choose(e);
    moments.append(moment);
    const row = document.createElement("button");
    row.className = "event" + (selected === e.id ? " selected" : "");
    const thumb = img.cloneNode();
    const info = document.createElement("div");
    info.className = "event-info";
    const timestamp = document.createElement("small");
    timestamp.textContent = fmt(e.start) + " source time";
    const heading = document.createElement("strong");
    heading.textContent = e.long_activity
      ? "Long activity"
      : e.label[0].toUpperCase() + e.label.slice(1);
    const desc = document.createElement("p");
    desc.textContent =
      e.note ||
      `${e.observations} observations over ${e.duration.toFixed(1)} seconds`;
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = e.category.toUpperCase();
    info.append(timestamp, heading, desc, tag);
    row.append(thumb, info);
    row.onclick = () => choose(e);
    events.append(row);
  }
  drawTimeline();
}
function renderDetails() {
  const e = result?.events.find((e) => e.id === selected);
  $("detailEmpty").hidden = !!e;
  $("detailContent").hidden = !e;
  $("detailBadge").textContent = e ? "TRACK #" + e.id : "SELECT A TRACK";
  if (!e) return;
  $("detailImage").src = `/api/jobs/${result.id}/media/${e.thumbnail}`;
  $("detailTitle").textContent = e.label;
  $("detailFacts").replaceChildren();
  for (const [k, v] of [
    ["Source time", fmt(e.start) + " - " + fmt(e.end)],
    ["Duration", e.duration.toFixed(1) + " seconds"],
    [
      "Confidence",
      e.confidence === null
        ? "Not provided (motion)"
        : Math.round(e.confidence * 100) + "%",
    ],
    ["Observations", String(e.observations)],
    ["Camera", result.camera],
  ]) {
    const dt = document.createElement("dt"),
      dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = v;
    $("detailFacts").append(dt, dd);
  }
  $("eventNote").value = e.note;
  $("eventTags").value = e.tags.join(", ");
  $("jumpSource").onclick = () => jump(e.start);
}
$("noteForm").onsubmit = async (event) => {
  event.preventDefault();
  if (!result || selected === null) return;
  const tags = $("eventTags")
    .value.split(",")
    .map((v) => v.trim())
    .filter(Boolean);
  try {
    const changed = await api(`/api/jobs/${result.id}/events/${selected}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: $("eventNote").value, tags }),
    });
    const index = result.events.findIndex((e) => e.id === selected);
    result.events[index] = changed;
    renderEvents();
    notify("Review note and tags saved.");
  } catch (e) {
    notify(e.message, true);
  }
};
function drawTimeline() {
  const canvas = $("timeline"),
    ctx = canvas.getContext("2d"),
    w = canvas.width,
    h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#12304d";
  ctx.fillRect(10, 22, w - 20, 18);
  const duration = result?.duration || 1;
  for (const e of filtered()) {
    ctx.fillStyle = color(e.id);
    ctx.globalAlpha = e.id === selected ? 0.95 : 0.65;
    ctx.fillRect(
      10 + (e.start / duration) * (w - 20),
      22,
      Math.max(3, (e.duration / duration) * (w - 20)),
      18,
    );
  }
  ctx.globalAlpha = 1;
  ctx.font = "13px system-ui";
  ctx.fillStyle = "#799bb9";
  for (let i = 0; i <= 6; i++)
    ctx.fillText(
      fmt((duration * i) / 6),
      Math.min(w - 36, 10 + ((w - 20) * i) / 6),
      69,
    );
  if (result && mode === "original") {
    const x = 10 + (video.currentTime / duration) * (w - 20);
    ctx.strokeStyle = "#d2e8ff";
    ctx.beginPath();
    ctx.moveTo(x, 13);
    ctx.lineTo(x, 47);
    ctx.stroke();
  }
}
$("timeline").onclick = (e) => {
  if (!result) return;
  const r = $("timeline").getBoundingClientRect();
  jump(
    Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * result.duration,
  );
};
function drawOverlay() {
  const w = video.videoWidth || 960,
    h = video.videoHeight || 540;
  overlay.width = w;
  overlay.height = h;
  const ctx = overlay.getContext("2d");
  if (!result || mode !== "original") return;
  const index = Math.min(
      result.frames.length - 1,
      Math.max(0, Math.floor(video.currentTime * result.sample_rate)),
    ),
    frame = result.frames[index];
  if (Math.abs(frame.time - video.currentTime) > 0.4 || !$("boxes").checked)
    return;
  for (const d of frame.detections) {
    const [x, y, bw, bh] = d.box;
    ctx.strokeStyle = color(d.id);
    ctx.lineWidth = d.id === selected ? 3 : 1.5;
    ctx.strokeRect(x * w, y * h, bw * w, bh * h);
    ctx.font = "12px system-ui";
    const label = "#" + d.id + " " + d.label;
    const tw = ctx.measureText(label).width + 10;
    ctx.fillStyle = color(d.id);
    ctx.fillRect(x * w, Math.max(0, y * h - 21), tw, 21);
    ctx.fillStyle = "#051524";
    ctx.fillText(label, x * w + 5, Math.max(14, y * h - 6));
  }
}
let lastTime = -1;
function animate() {
  drawOverlay();
  if (video.currentTime !== lastTime) {
    drawTimeline();
    lastTime = video.currentTime;
  }
  requestAnimationFrame(animate);
}
animate();
render();
refreshRecordings().catch((e) => notify(e.message, true));
const saved = localStorage.getItem("synopsis-recording");
if (saved) watch({ id: saved });
