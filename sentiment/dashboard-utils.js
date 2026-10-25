/* Small, dependency-free helpers shared by the standalone sentiment page and its tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SentimentUtils = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const STATES = ['Abia','Adamawa','Akwa Ibom','Anambra','Bauchi','Bayelsa','Benue','Borno','Cross River','Delta','Ebonyi','Edo','Ekiti','Enugu','FCT','Gombe','Imo','Jigawa','Kaduna','Kano','Katsina','Kebbi','Kogi','Kwara','Lagos','Nasarawa','Niger','Ogun','Ondo','Osun','Oyo','Plateau','Rivers','Sokoto','Taraba','Yobe','Zamfara'];
  const ZONES = ['NC','NE','NW','SE','SS','SW'];
  const TONES = ['cold','cool','mixed','warm'];
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  async function fetchJson(url, {timeoutMs = 15000, cache = 'no-cache'} = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {signal: controller.signal, cache});
      if (!response.ok) throw new Error('HTTP ' + response.status + ' loading ' + url);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }
  function validateData(data) {
    if (!data || !Array.isArray(data.states) || !Array.isArray(data.zones)) throw new Error('Missing state or zone data');
    const states = new Map(data.states.map(s => [String(s?.name || '').toLowerCase(), s]));
    const zones = new Map(data.zones.map(z => [z?.id, z]));
    if (data.states.length !== 37 || states.size !== 37 || STATES.some(n => !states.has(n.toLowerCase()))) throw new Error('Expected all 37 unique states and FCT');
    if (data.zones.length !== 6 || zones.size !== 6 || ZONES.some(n => !zones.has(n))) throw new Error('Expected all six unique zones');
    for (const s of data.states) {
      if (!zones.has(s.zone) || !TONES.includes(s.president) || !TONES.includes(s.nsa) || typeof s.note !== 'string') throw new Error('Invalid state assessment: ' + s.name);
    }
    for (const z of data.zones) {
      if (['approve','disapprove','unsafe'].some(k => !Number.isFinite(z[k]) || z[k] < 0 || z[k] > 100)) throw new Error('Invalid zone data: ' + z.id);
    }
    if (!data.subjects || typeof data.subjects.president !== 'string' || typeof data.subjects.nsa !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(data.assessment_updated || data.updated || '')) throw new Error('Missing assessment metadata');
    return {states, zones};
  }
  function geoName(props) {
    const raw = props?.admin1Name || props?.name || '';
    return ({'Federal Capital Territory':'FCT',Fct:'FCT',Nassarawa:'Nasarawa'})[raw] || raw;
  }
  function validateGeo(geo, states) {
    if (geo?.type !== 'FeatureCollection' || !Array.isArray(geo.features)) throw new Error('Invalid state boundaries');
    const seen = new Set();
    for (const f of geo.features) {
      const name = geoName(f.properties).toLowerCase();
      if (!states.has(name) || seen.has(name)) throw new Error('Unmatched or duplicate state boundary: ' + name);
      seen.add(name);
      const g = f.geometry;
      if (!g || !['Polygon','MultiPolygon'].includes(g.type)) throw new Error('Invalid geometry: ' + name);
      const polygons = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
      if (!Array.isArray(polygons) || !polygons.length || polygons.some(p => !Array.isArray(p) || !p.length || p.some(r => !Array.isArray(r) || r.length < 4 || r.some(c => !Array.isArray(c) || !Number.isFinite(c[0]) || !Number.isFinite(c[1]) || Math.abs(c[0]) > 180 || Math.abs(c[1]) > 90)))) throw new Error('Invalid coordinates: ' + name);
    }
    if (seen.size !== 37) throw new Error('Incomplete state boundaries');
    return geo;
  }
  function ringContains(ring, x, y) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function featureContains(feature, ll) {
    const g = feature?.geometry;
    if (!g || !['Polygon','MultiPolygon'].includes(g.type)) return false;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    return polys.some(poly => ringContains(poly[0], ll.lng, ll.lat) && !poly.slice(1).some(hole => ringContains(hole, ll.lng, ll.lat)));
  }
  function heatPoints(data) {
    if (!Array.isArray(data?.points) || !data.points.length || data.count !== data.points.length || !/^\d{4}-\d{2}-\d{2}$/.test(data.from || '') || !/^\d{4}-\d{2}-\d{2}$/.test(data.to || '') || data.from > data.to) throw new Error('Missing or inconsistent heat data/window');
    return data.points.map(p => {
      const [lat, lng, count] = Array.isArray(p) ? p : [p?.la, p?.lo, p?.n];
      if (![lat,lng,count].every(Number.isFinite) || Math.abs(lat) > 90 || Math.abs(lng) > 180 || count < 0) throw new Error('Invalid heat point');
      return [lat, lng, Math.min(1, count / 20)];
    });
  }
  function validateX(xs) {
    if (!xs || typeof xs.window !== 'string' || typeof xs.caveat !== 'string' || !xs.glossary || typeof xs.glossary !== 'object') throw new Error('Invalid historical X sample');
    for (const group of [xs.states, xs.zones]) {
      if (!group || typeof group !== 'object' || Array.isArray(group)) throw new Error('Invalid historical X group');
      for (const item of Object.values(group)) for (const key of ['president','nsa']) {
        if (!item || (item[key] !== undefined && (!Array.isArray(item[key]) || item[key].some(v => typeof v !== 'string')))) throw new Error('Invalid historical X themes');
      }
    }
    return xs;
  }
  return {escapeHtml, fetchJson, validateData, geoName, validateGeo, featureContains, heatPoints, validateX};
});
