const form = document.querySelector("#scanForm");
const results = document.querySelector("#results");
const ideas = document.querySelector("#ideas");
const notes = document.querySelector("#notes");
const topScore = document.querySelector("#topScore");
const ideaCount = document.querySelector("#ideaCount");
const updatedAt = document.querySelector("#updatedAt");
const providerStatus = document.querySelector("#providerStatus");

async function loadHealth() {
  const response = await fetch("/api/health");
  const health = await response.json();
  const marketData = health.cutemarkets_configured ? "CuteMarkets delayed data" : "No market data provider";
  const featuredIdeas = health.market_chameleon_configured ? "Market Chameleon configured" : "No featured-ideas feed";
  providerStatus.textContent = `${marketData} - ${featuredIdeas}`;
}

async function scan(event) {
  event?.preventDefault();
  results.innerHTML = `<div class="card">Scanning option chains...</div>`;

  const params = new URLSearchParams({
    symbols: document.querySelector("#symbols").value,
    window: document.querySelector("#window").value,
    strategy: document.querySelector("#strategy").value,
    limit: "12",
  });
  const response = await fetch(`/api/spreads/recommendations?${params.toString()}`);
  if (!response.ok) {
    results.innerHTML = `<div class="card warning">Scan failed: ${await response.text()}</div>`;
    return;
  }
  render(await response.json());
}

function render(payload) {
  topScore.textContent = payload.candidates[0]?.total_score?.toFixed?.(1) ?? "--";
  ideaCount.textContent = payload.featured_ideas.length;
  updatedAt.textContent = new Date(payload.generated_at).toLocaleTimeString();

  results.innerHTML = payload.candidates.length
    ? payload.candidates.map(renderCandidate).join("")
    : `<div class="card">No spreads matched this window.</div>`;

  ideas.innerHTML = payload.featured_ideas.length
    ? payload.featured_ideas.map(renderIdea).join("")
    : `<div class="card">No featured ideas loaded.</div>`;

  notes.innerHTML = payload.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("");
}

function renderCandidate(candidate) {
  const price = candidate.net_credit
    ? `Credit $${candidate.net_credit.toFixed(2)}`
    : `Debit $${candidate.net_debit.toFixed(2)}`;
  return `
    <article class="card">
      <div class="cardHead">
        <div>
          <div class="symbol">${candidate.symbol}</div>
          <div class="strategy">${label(candidate.strategy)} - ${candidate.expiration}</div>
        </div>
        <div class="score">${candidate.total_score.toFixed(1)}</div>
      </div>
      <div class="legs">
        <div class="metric"><span>Long</span><strong>${candidate.long_leg.strike} ${candidate.long_leg.option_type}</strong></div>
        <div class="metric"><span>Short</span><strong>${candidate.short_leg.strike} ${candidate.short_leg.option_type}</strong></div>
        <div class="metric"><span>Entry</span><strong>${price}</strong></div>
        <div class="metric"><span>Breakeven</span><strong>$${candidate.breakeven.toFixed(2)}</strong></div>
      </div>
      <div class="metrics">
        <div class="metric"><span>Max Profit</span><strong>$${(candidate.max_profit * 100).toFixed(0)}</strong></div>
        <div class="metric"><span>Max Loss</span><strong>$${(candidate.max_loss * 100).toFixed(0)}</strong></div>
        <div class="metric"><span>R/R</span><strong>${candidate.reward_to_risk}:1</strong></div>
        <div class="metric"><span>Liquidity</span><strong>${candidate.liquidity_score.toFixed(1)}</strong></div>
      </div>
      <p class="rationale">${candidate.rationale.map(escapeHtml).join(" - ")}</p>
      ${
        candidate.warnings.length
          ? `<p class="rationale warning">${candidate.warnings.map(escapeHtml).join(" - ")}</p>`
          : ""
      }
    </article>
  `;
}

function renderIdea(idea) {
  const title = escapeHtml(idea.title);
  const href = idea.url ? `<a href="${idea.url}" target="_blank" rel="noreferrer">${title}</a>` : title;
  return `
    <article class="card">
      <div class="cardHead">
        <div>
          <div class="symbol">${escapeHtml(idea.symbol)}</div>
          <div class="strategy">${escapeHtml(idea.strategy)}${idea.expiration ? ` - ${idea.expiration}` : ""}</div>
        </div>
      </div>
      <p class="rationale">${href}</p>
    </article>
  `;
}

function label(value) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return map[character];
  });
}

form.addEventListener("submit", scan);
loadHealth().then(scan).catch((error) => {
  providerStatus.textContent = "Could not load provider status";
  results.innerHTML = `<div class="card warning">${escapeHtml(error.message)}</div>`;
});
