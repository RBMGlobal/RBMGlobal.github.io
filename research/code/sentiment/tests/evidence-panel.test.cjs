const test = require('node:test');
const assert = require('node:assert/strict');
const panel = require('../integration/evidence-panel.js');
test('existing Yiaga-style record retains results, sample, caveat and original dates', () => {
  const html = panel.renderHtml({assessment_updated:'2026-09-27',national_evidence:[{source:'Historical survey',url:'https://primary.example/report.pdf',publication_date:'2026-10-07',fieldwork_month:'2026-09',fieldwork_exact_dates:null,fieldwork_note:'Exact days not disclosed',sample:2470,population:'Nigerian adults',results:{president_approve:22},selection:'Mixed mode',weighting:'Sex within zone',limitations:'Nonprobability rural sample',comparability:'Not comparable',reviewed:'2026-10-12',pages:[14,21]}]},null);
  for (const value of ['2026-09-27','2026-10-07','2026-09','2470','22%','Nonprobability','2026-10-12','14, 21','Historical X themes remain unverified','unavailable']) assert.ok(html.includes(value), value);
});
test('all model and source text is escaped and unsafe links are inert', () => {
  const attack = '<img src=x onerror=alert(1)>';
  const html = panel.renderHtml({updated:attack,national_evidence:[{source:attack,url:'javascript:alert(1)',results:{[attack]:22},limitations:attack}]},{schema_version:1,sources:[],review_date:attack,summary:attack,security_context:[{title:attack,summary:attack}]});
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes('&lt;img'));
  assert.equal(panel.safeUrl('https://user:pass@example.com'), null);
});
test('zonal evidence and sourced security context are visible without changing state semantics', () => {
  const html = panel.renderHtml({assessment_updated:'2026-09-27',zonal_evidence:[{source:'Zonal report',geography:{name:'North East'},sample:410,result_details:[{label:'Presidential approval',value:39}]}]}, {schema_version:1,sources:[{id:'s',url:'https://agency.example/report',publisher:'Primary agency'}],review_date:'2026-10-25',summary:'No rating changes',scope:'Supplied sources',security_context:[{source_id:'s',title:'Displacement',summary:'Reported by agency',geography:'Nigeria',publication_date:'2026-10-20',event_window:{from:'2026-10-01',to:'2026-10-19',note:'Reporting period'}}],state_changes:[],withheld_state_changes:[],caveat:'Not polling'});
  for (const value of ['North East','410','39%','Displacement','2026-10-19','Primary agency','not opinion polling','were not changed']) assert.ok(html.includes(value),value);
});
test('optional review failure preserves evidence and readable unavailable status', async () => {
  const elements = {'national-evidence':{innerHTML:''}, 'evidence-freshness':{textContent:''}};
  global.document = {getElementById: id => elements[id]};
  await panel.load({assessment_updated:'2026-09-27',freshness_note:'Original source windows',national_evidence:[{source:'Retained evidence',results:{approve:22}}]}, async()=>{throw Error('offline');});
  assert.ok(elements['national-evidence'].innerHTML.includes('Retained evidence'));
  assert.ok(elements['national-evidence'].innerHTML.includes('unavailable'));
  assert.equal(elements['evidence-freshness'].textContent,'Original source windows');
  delete global.document;
});
