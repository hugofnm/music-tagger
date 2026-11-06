const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDur = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "–");
const pct = (x) => (x == null ? "" : `${Math.round(x * 100)}%`);

const STATUS = { untagged: "à traiter", matched: "matché", tagged: "taggé", nomatch: "sans résultat", error: "erreur" };
const DEFAULT_OPTS = { mode: "overwrite", embed_cover: true, cover_file: true, rename: false, tag_unmatched: true };

let albums = [];
let current = null; // détail de l'album sélectionné
let filter = "all";
let lastJobRunning = false;

function opts() {
  try { return { ...DEFAULT_OPTS, ...JSON.parse(localStorage.getItem("mt-opts") || "{}") }; }
  catch { return { ...DEFAULT_OPTS }; }
}
function saveOpts(o) { try { localStorage.setItem("mt-opts", JSON.stringify(o)); } catch {} }

async function api(path, options = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ""), err ? 6000 : 3000);
}

function confColor(c) { return c >= 0.85 ? "var(--ok)" : c >= 0.65 ? "var(--warn)" : "var(--bad)"; }
function scoreClass(s) { return s >= 0.85 ? "s-hi" : s >= 0.65 ? "s-mid" : "s-lo"; }

// ------------------------------------------------------------------ liste
function badge(a) {
  const low = a.status === "matched" && a.confidence < 0.85 ? " low" : "";
  const extra = a.status === "matched" && a.confidence != null ? ` ${pct(a.confidence)}` : "";
  return `<span class="badge b-${a.status}${low}">${STATUS[a.status] || a.status}${extra}</span>`;
}

function thumb(a) {
  if (a.deezer?.cover) return `<img loading="lazy" src="${esc(a.deezer.cover)}" alt="" onerror="this.style.visibility='hidden'">`;
  if (a.has_cover) return `<img loading="lazy" src="/api/albums/${a.id}/cover" alt="" onerror="this.style.visibility='hidden'">`;
  return `<div class="ph">♪</div>`;
}

function renderList() {
  const q = $("#filter-text").value.trim().toLowerCase();
  const ul = $("#albums");
  const items = albums.filter((a) => {
    if (q && !`${a.artist} ${a.title} ${a.dir}`.toLowerCase().includes(q)) return false;
    if (filter === "todo") return a.status === "untagged";
    if (filter === "matched") return a.status === "matched";
    if (filter === "tagged") return a.status === "tagged";
    if (filter === "problem") return ["nomatch", "error"].includes(a.status) || (a.status === "matched" && a.confidence < 0.85);
    return true;
  });
  ul.innerHTML = items.map((a) => `
    <li data-id="${a.id}" class="${current?.id === a.id ? "sel" : ""}">
      ${thumb(a)}
      <div class="meta"><div class="t">${esc(a.title)}</div><div class="a">${esc(a.artist)} · ${a.n_tracks} pistes</div></div>
      ${badge(a)}
    </li>`).join("") || `<li class="muted" style="cursor:default;grid-template-columns:1fr">Aucun album</li>`;
}

async function loadAlbums() {
  albums = await api("/api/albums");
  renderList();
}

// ------------------------------------------------------------------ détail
async function openAlbum(id) {
  try {
    current = await api(`/api/albums/${id}`);
    renderDetail();
    renderList();
  } catch (e) { toast(e.message, true); }
}

function updateListItem(d) {
  const i = albums.findIndex((a) => a.id === d.id);
  if (i >= 0) {
    const { tracks, match, backups, ...summary } = d;
    albums[i] = summary;
  }
  renderList();
}

function renderDetail() {
  const d = current;
  const root = $("#detail");
  root.innerHTML = "";
  root.append($("#tpl-detail").content.cloneNode(true));
  const k = (name) => root.querySelector(`[data-k="${name}"]`);
  root.querySelectorAll(".cover img").forEach((img) => (img.onerror = () => (img.style.visibility = "hidden")));

  k("title").textContent = d.title;
  k("artist").textContent = d.artist;
  k("dir").textContent = d.dir;
  k("ntracks").textContent = `${d.n_tracks} fichiers · ${d.has_cover ? "pochette présente" : "pas de pochette"}`;
  if (d.has_cover) k("localcover").src = `/api/albums/${d.id}/cover?t=${Date.now()}`;
  else k("localcover").replaceWith(Object.assign(document.createElement("span"), { textContent: "♪" }));

  const m = d.match;
  if (m) {
    const al = m.album;
    k("dzcover").src = al.cover_big || al.cover_medium || "";
    k("dztitle").textContent = al.title;
    k("dzartist").textContent = al.artist?.name || "";
    const genres = (al.genres?.data || []).map((g) => g.name).join(", ");
    k("dzmeta").textContent = [al.release_date?.slice(0, 4), al.label, genres, `${m.tracks.length} pistes`].filter(Boolean).join(" · ");
    k("dzlink").href = al.link || `https://www.deezer.com/album/${al.id}`;
    k("conf").textContent = `confiance ${pct(m.confidence)}`;
    k("confbar").style.width = pct(m.confidence);
    k("confbar").style.background = confColor(m.confidence);
  } else {
    k("dztitle").textContent = d.status === "nomatch" ? "Aucun résultat" : "Pas encore matché";
    k("dzartist").textContent = "Lance « Matcher auto » ou cherche manuellement.";
    k("dzcover").replaceWith(Object.assign(document.createElement("span"), { textContent: "?" }));
    k("dzlink").remove();
    root.querySelector(".conf").remove();
  }

  k("message").textContent = d.message || "";
  root.querySelector('[data-a="apply"]').disabled = !m;
  root.querySelector('[data-a="restore"]').classList.toggle("hidden", !d.backups.length);

  // options
  const o = opts();
  root.querySelectorAll(".opts input").forEach((inp) => {
    if (inp.type === "radio") inp.checked = o[inp.name] === inp.value;
    else inp.checked = !!o[inp.name];
    inp.addEventListener("change", () => {
      const cur = opts();
      cur[inp.name] = inp.type === "radio" ? inp.value : inp.checked;
      saveOpts(cur);
    });
  });

  // pistes
  const remote = m ? m.tracks : [];
  const byId = Object.fromEntries(remote.map((t) => [t.id, t]));
  const multiDisc = remote.some((t) => (t.disk_number || 1) > 1);
  const label = (t) => `${multiDisc ? (t.disk_number || 1) + "-" : ""}${t.track_position}. ${t.title}${t.artist && t.artist !== m.album.artist?.name ? " — " + t.artist : ""}`;
  k("tbody").innerHTML = d.tracks.map((t, i) => {
    const mp = m?.mapping[i];
    const r = mp ? byId[mp.track_id] : null;
    const num = `${t.discnumber && multiDisc ? t.discnumber + "-" : ""}${t.tracknumber ?? "?"}`;
    const select = m ? `<select data-i="${i}">
        <option value="">— ne pas associer —</option>
        ${remote.map((rt) => `<option value="${rt.id}" ${r && r.id === rt.id ? "selected" : ""}>${esc(label(rt))}</option>`).join("")}
      </select>` : `<span class="muted">–</span>`;
    const changed = r && r.title !== t.title ? "changed" : "";
    const off = r && Math.abs((r.duration || 0) - t.duration) > 5 ? "off" : "";
    const dur = r ? `${fmtDur(t.duration)} / ${fmtDur(r.duration)}` : fmtDur(t.duration);
    const score = mp ? `<span class="score ${scoreClass(mp.score)}">${mp.manual ? "manuel" : pct(mp.score)}</span>` : "";
    return `<tr>
      <td class="muted">${esc(num)}</td>
      <td class="file" title="${esc(t.rel)}">${esc(t.filename)}${t.error ? ` <span class="s-lo">⚠ ${esc(t.error)}</span>` : ""}</td>
      <td class="${changed}">${esc(t.title)}</td>
      <td>${select}</td>
      <td class="dur ${off}">${dur}</td>
      <td>${score}</td>
    </tr>`;
  }).join("");
}

async function act(action, el) {
  const d = current;
  if (!d) return;
  try {
    if (action === "automatch") {
      el.disabled = true; el.textContent = "Recherche…";
      current = await api(`/api/albums/${d.id}/match`, { method: "POST", body: "{}" });
      updateListItem(current); renderDetail();
    } else if (action === "togglesearch") {
      const box = $('[data-k="searchbox"]');
      box.classList.toggle("hidden");
      const inp = box.querySelector("input");
      if (!inp.value) inp.value = `${d.artist} ${d.title}`;
      inp.focus();
    } else if (action === "apply") {
      el.disabled = true; el.textContent = "Écriture…";
      const res = await api(`/api/albums/${d.id}/apply`, { method: "POST", body: JSON.stringify(opts()) });
      current = res.album;
      updateListItem(current); renderDetail();
      const w = res.result.warnings;
      toast(`${res.result.written} fichier(s) taggé(s)${res.result.cover ? " + pochette" : ""}${w.length ? " · " + w.join(" ; ") : ""}`, w.length > 0);
    } else if (action === "restore") {
      if (!confirm("Restaurer les tags d'origine de cet album (dernière sauvegarde) ?")) return;
      current = await api(`/api/albums/${d.id}/restore`, { method: "POST" });
      updateListItem(current); renderDetail();
      toast("Tags d'origine restaurés");
    }
  } catch (e) {
    toast(e.message, true);
    if (current) renderDetail();
  }
}

async function doSearch(form) {
  const q = form.q.value.trim();
  const box = $('[data-k="results"]');
  // URL ou ID Deezer collé : on matche directement
  if (/deezer\.com\/.*album\/\d+/.test(q) || /^\d+$/.test(q)) return chooseCandidate(q);
  box.innerHTML = `<p class="muted">Recherche…</p>`;
  try {
    const res = await api(`/api/albums/${current.id}/search?q=${encodeURIComponent(q)}`);
    box.innerHTML = res.map((c) => `
      <div class="result" data-dz="${c.id}" title="${esc(c.title)} — ${esc(c.artist)}">
        <img loading="lazy" src="${esc(c.cover)}" alt="" onerror="this.style.visibility='hidden'">
        <div class="t">${esc(c.title)}</div>
        <div class="a">${esc(c.artist)}</div>
        <div class="a">${c.nb_tracks ?? "?"} pistes · ${esc(c.record_type || "")} · <span class="${scoreClass(c.score)}">${pct(c.score)}</span></div>
      </div>`).join("") || `<p class="muted">Aucun résultat.</p>`;
  } catch (e) { box.innerHTML = ""; toast(e.message, true); }
}

async function chooseCandidate(dzId) {
  try {
    current = await api(`/api/albums/${current.id}/match`, { method: "POST", body: JSON.stringify({ deezer_id: String(dzId) }) });
    updateListItem(current); renderDetail();
    toast("Album Deezer sélectionné — vérifie les pistes puis « Écrire les tags »");
  } catch (e) { toast(e.message, true); }
}

async function setMapping(i, trackId) {
  try {
    current = await api(`/api/albums/${current.id}/mapping`, {
      method: "POST", body: JSON.stringify({ index: i, track_id: trackId ? Number(trackId) : null }),
    });
    renderDetail();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ statut / tâches
const JOBNAMES = { scan: "Scan du dossier", match: "Recherche Deezer", apply: "Écriture des tags" };

async function pollStatus() {
  try {
    const s = await api("/api/status");
    $("#musicdir").textContent = s.music_dir;
    const c = s.counts;
    $("#counts").innerHTML = `<span><b>${s.total}</b> albums</span>` +
      Object.entries(STATUS).filter(([key]) => c[key]).map(([key, lbl]) => `<span><b>${c[key]}</b> ${lbl}</span>`).join("");
    const j = s.job;
    const bar = $("#jobbar");
    bar.classList.toggle("hidden", !j.running);
    ["#btn-scan", "#btn-match-all", "#btn-apply-all"].forEach((b) => ($(b).disabled = j.running));
    if (j.running) {
      $("#jobname").textContent = `${JOBNAMES[j.name] || j.name} ${j.total ? `(${j.done}/${j.total})` : ""}`;
      $("#jobcurrent").textContent = j.current || "";
      $("#jobprogress").style.width = j.total ? `${(100 * j.done) / j.total}%` : "5%";
    }
    if (j.running || lastJobRunning) await loadAlbums();
    if (lastJobRunning && !j.running) {
      if (j.errors?.length) toast(j.errors.slice(0, 3).join(" · "), true);
      else toast(`${JOBNAMES[j.name] || "Tâche"} terminé(e)`);
      if (current) openAlbum(current.id);
    }
    lastJobRunning = j.running;
  } catch (e) { /* serveur en redémarrage */ }
  setTimeout(pollStatus, lastJobRunning ? 1000 : 4000);
}

async function startJob(path, body) {
  try {
    const r = await api(path, { method: "POST", body: body ? JSON.stringify(body) : "{}" });
    if (r.queued === 0) toast("Rien à traiter");
    lastJobRunning = true;
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ événements
$("#albums").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) openAlbum(li.dataset.id);
});
$("#filter-text").addEventListener("input", renderList);
$("#chips").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  filter = b.dataset.f;
  document.querySelectorAll("#chips button").forEach((x) => x.classList.toggle("on", x === b));
  renderList();
});
$("#detail").addEventListener("click", (e) => {
  const b = e.target.closest("[data-a]");
  if (b && b.tagName === "BUTTON") return act(b.dataset.a, b);
  const r = e.target.closest(".result[data-dz]");
  if (r) chooseCandidate(r.dataset.dz);
});
$("#detail").addEventListener("submit", (e) => {
  e.preventDefault();
  doSearch(e.target);
});
$("#detail").addEventListener("change", (e) => {
  if (e.target.matches("select[data-i]")) setMapping(Number(e.target.dataset.i), e.target.value);
});
$("#btn-scan").onclick = () => startJob("/api/scan");
$("#btn-match-all").onclick = () => startJob("/api/batch/match");
$("#btn-apply-all").onclick = () => {
  const th = Math.min(100, Math.max(50, Number($("#threshold").value) || 85));
  const n = albums.filter((a) => a.status === "matched" && a.confidence >= th / 100).length;
  if (!n) return toast(`Aucun album matché avec une confiance ≥ ${th}%`);
  if (!confirm(`Écrire les tags de ${n} album(s) avec une confiance ≥ ${th}% ?\n(Une sauvegarde des tags d'origine est faite pour chaque album.)`)) return;
  startJob("/api/batch/apply", { ...opts(), min_confidence: th / 100 });
};

loadAlbums();
pollStatus();
