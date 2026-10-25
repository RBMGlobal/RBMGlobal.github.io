(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SentimentEvidence = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const list = value => Array.isArray(value) ? value : [];
  function safeUrl(value) {
    try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; }
    catch (_) { return null; }
  }
  function link(value, label) {
    const url = safeUrl(value);
    return url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(label) + '</a>' : esc(label);
  }
  function fieldwork(record) {
    const days = record.fieldwork_exact_dates;
    if (days && typeof days === 'object' && days.from && days.to) return days.from + ' to ' + days.to;
    return record.fieldwork_month || 'Exact window not disclosed';
  }
  function evidenceHtml(record) {
    const geography = record.geography && record.geography.name || 'Nigeria (national)';
    const details = list(record.result_details);
    const results = details.length ? details.map(r => esc(r.label) + ': ' + esc(r.value) + '%').join(' · ') : Object.entries(record.results || {}).map(([label, value]) => esc(label.replace(/_/g, ' ')) + ': ' + esc(value) + '%').join(' · ');
    return '<section style="border-top:1px solid #b9b3a8;padding-top:8px;margin-top:10px">' +
      '<p><b>' + esc(record.source || 'Published survey evidence') + '</b> · ' + esc(geography) + '</p>' +
      '<p>' + results + '</p>' +
      '<p><b>Dates and sample:</b> published ' + esc(record.publication_date || 'not recorded') + '; fieldwork ' + esc(fieldwork(record)) + '. ' + esc(record.fieldwork_note || '') + ' Sample: ' + esc(record.sample) + '. ' + esc(record.population || '') + '</p>' +
      '<p><b>Method:</b> ' + esc(record.selection || '') + ' Weighting: ' + esc(record.weighting || 'not disclosed') + '</p>' +
      '<p><b>Limits:</b> ' + esc(record.limitations || 'See primary source.') + '</p>' +
      '<p><b>Comparability:</b> ' + esc(record.comparability || 'No like-for-like trend is inferred.') + '</p>' +
      '<p>' + link(record.url, 'Primary report') + (list(record.pages).length ? ' · pages ' + esc(record.pages.join(', ')) : '') + ' · Evidence reviewed ' + esc(record.reviewed || 'not recorded') + '</p></section>';
  }
  function reviewHtml(review) {
    if (review && review.status === 'awaiting_activation') return '<p>Automated source reviews are awaiting API activation. Published evidence and assessment dates are unchanged.</p>';
    if (!review || review.schema_version !== 1 || !Array.isArray(review.sources)) return '<p>Automatic review record unavailable. Existing evidence and assessment dates remain visible.</p>';
    const sources = new Map(review.sources.map(s => [s.id, s]));
    const context = list(review.security_context).map(item => {
      const source = sources.get(item.source_id) || {};
      const window = item.event_window || {};
      return '<section><p><b>' + esc(item.title) + '</b> · ' + esc(item.geography) + '</p><p>' + esc(item.summary) + '</p><p>Attributed to ' + link(source.url, source.publisher || item.publisher || 'source') + '; published ' + esc(item.publication_date || source.published_at) + '. Reporting window: ' + esc(window.from && window.to ? window.from + ' to ' + window.to : 'not disclosed') + '. ' + esc(window.note || '') + '</p></section>';
    }).join('');
    const changes = list(review.state_changes).length;
    return '<section><p><b>Evidence review:</b> ' + esc(review.review_date) + ' · ' + esc(review.summary) + '</p><p>' + esc(review.scope) + '</p>' +
      (context ? '<p><b>Public-security context</b> — source-attributed reporting, not opinion polling.</p>' + context : '') +
      (changes ? '<p>' + changes + ' state rating change(s) passed explicit, geography-specific review. Other ratings retain their original source basis.</p>' : '<p>State ratings were not changed by this review.</p>') +
      (list(review.withheld_state_changes).length ? '<p>' + list(review.withheld_state_changes).length + ' proposed state change(s) withheld for insufficient approval or evidence.</p>' : '') +
      '<p>' + esc(review.caveat) + '</p></section>';
  }
  function renderHtml(data, review) {
    const evidence = [...list(data.national_evidence), ...list(data.zonal_evidence), ...list(data.state_evidence)].slice().reverse();
    return '<summary style="cursor:pointer"><b>Source evidence &amp; latest review</b></summary><div style="max-height:36vh;overflow:auto;padding-top:8px">' +
      '<p>State assessment: ' + esc(data.assessment_updated || data.updated) + '. National and zonal surveys are separate evidence; their percentages are never assigned to states. Historical X themes remain unverified.</p>' +
      reviewHtml(review) + evidence.map(evidenceHtml).join('') + '</div>';
  }
  function render(data, review) {
    const panel = document.getElementById('national-evidence');
    if (panel) panel.innerHTML = renderHtml(data, review);
    const note = document.getElementById('evidence-freshness');
    if (note) note.textContent = data.freshness_note || 'Assessment dates and source evidence windows are separate.';
  }
  async function load(data, fetchJson) {
    // Evidence in data.json remains usable when the optional review fails.
    render(data, null);
    try { render(data, await fetchJson('review.json')); }
    catch (_) { /* Keep explicit unavailable status and the complete evidence. */ }
  }
  return {renderHtml, evidenceHtml, reviewHtml, safeUrl, load};
});
