'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const utilsText = fs.readFileSync(path.join(root,'dashboard-utils.js'),'utf8');
const U = require('../dashboard-utils.js');
const html = fs.readFileSync(path.join(root,'index.html'),'utf8');
const app = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]).filter(Boolean).join('\n');
const data = JSON.parse(fs.readFileSync(path.join(root,'data.json'),'utf8'));
const rich = html.includes('id="state-jump"');
const copy = x => JSON.parse(JSON.stringify(x));
const rect = (a,b,c,d)=>[[a,b],[c,b],[c,d],[a,d],[a,b]];
const geo = {type:'FeatureCollection',features:data.states.map(s=>({type:'Feature',properties:{name:s.name},geometry:{type:'Polygon',coordinates:[rect(0,0,2,2)]}}))};
const flush = async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};

function start({stateData=data, boundary=geo, x='pending', heat='fail', leaflet=true}={}) {
  const nodes=new Map(),requests=[],layers=[],frames=[];
  const node = id => {
    if(nodes.has(id))return nodes.get(id);
    const classes=new Set();
    const n={id,value:'',innerHTML:'',textContent:'',style:{},attributes:{},dataset:{},classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c)},setAttribute(k,v){this.attributes[k]=v},querySelectorAll:()=>[],click(){this.onclick?.()}};
    nodes.set(id,n);return n;
  };
  const buttons=['president','nsa'].map(k=>{const n=node(k);n.dataset.layer=k;return n;});
  const document={getElementById:node,querySelectorAll:s=>s.startsWith('button.layer')?buttons:[],addEventListener(){}};
  const attached=new Set(),panes=new Map();
  const map={setView(){return this},createPane(n){panes.set(n,{style:{}})},getPane:n=>panes.get(n),fitBounds(){},on(){},removeLayer:l=>attached.delete(l),hasLayer:l=>attached.has(l)};
  const layer=()=>({addTo(){attached.add(this);return this},setOpacity(){}});
  const L={map:()=>map,control:{zoom:()=>layer()},tileLayer:()=>layer(),svg:()=>({}),DomEvent:{stopPropagation(){}},geoJSON:(g,opts)=>{const ls=g.features.map(f=>{const l={feature:f,styles:[],setStyle(s){this.styles.push(s)},bindTooltip(){},on(){},getBounds(){return{contains:()=>true}}};opts.onEachFeature(f,l);return l});layers.push(...ls);return Object.assign(layer(),{eachLayer:f=>ls.forEach(f),getBounds:()=>({})})}};
  const context=vm.createContext({document,console:{error(){}},AbortController,setTimeout:(...args)=>{const timer=setTimeout(...args);timer.unref();return timer},clearTimeout,SentimentUtils:U,requestAnimationFrame:fn=>{frames.push(fn);return frames.length},fetch:async(url,opts)=>{
    requests.push({url,opts});
    let result=url==='data.json'?stateData:url.includes('geojson')?boundary:url==='x_sample.json'?x:heat;
    if(result==='pending')return new Promise(()=>{});
    if(result==='fail')return {ok:false,status:503};
    return {ok:true,status:200,json:async()=>copy(result)};
  },...(leaflet?{L}:{})});
  // Use the browser build so fetchJson resolves the isolated fetch implementation.
  vm.runInContext(utilsText,context);
  let error;try{vm.runInContext(app,context)}catch(e){error=e}
  return {context,nodes,node,requests,layers,frames,error,run:code=>vm.runInContext(code,context),buttons};
}

test('published assessments retain all 37 states and six valid zones',()=>{
 const before=JSON.stringify(data),index=U.validateData(data);
 assert.equal(index.states.size,37);assert.equal(index.zones.size,6);assert.ok(data.assessment_updated);assert.equal(JSON.stringify(data),before);
});
test('incomplete, duplicate, unknown-zone and invalid-rating data are refused',()=>{
 for(const mutate of [d=>d.states.pop(),d=>d.states[1]=d.states[0],d=>d.states[0].zone='??',d=>d.states[0].president="warm' onclick='evil",d=>d.zones[0].unsafe=101]){
  const d=copy(data);mutate(d);assert.throws(()=>U.validateData(d));
 }
});
test('all 37 boundaries must match; aliases, missing states and invalid coordinates are checked',()=>{
 const idx=U.validateData(data);assert.equal(U.validateGeo(copy(geo),idx.states).features.length,37);
 assert.equal(U.geoName({admin1Name:'Federal Capital Territory'}),'FCT');
 assert.equal(U.geoName({name:'Nassarawa'}),'Nasarawa');
 const missing=copy(geo);missing.features.pop();assert.throws(()=>U.validateGeo(missing,idx.states));
 const broken=copy(geo);broken.features[0].geometry.coordinates[0][0][0]=Infinity;assert.throws(()=>U.validateGeo(broken,idx.states));
});
test('fallback geographic selection excludes holes and supports multipolygons',()=>{
 const feature={geometry:{type:'Polygon',coordinates:[rect(0,0,10,10),rect(3,3,7,7)]}};
 assert.equal(U.featureContains(feature,{lng:1,lat:1}),true);assert.equal(U.featureContains(feature,{lng:5,lat:5}),false);
 feature.geometry={type:'MultiPolygon',coordinates:[[rect(0,0,2,2)],[rect(8,8,10,10)]]};
 assert.equal(U.featureContains(feature,{lng:9,lat:9}),true);assert.equal(U.featureContains(feature,{lng:5,lat:5}),false);
 assert.equal(U.featureContains({geometry:{type:'Point',coordinates:[1,1]}},{lng:1,lat:1}),false);
});
test('heat data require real coordinates, counts and an ordered source window; zero stays zero',()=>{
 const heat={from:'2026-10-01',to:'2026-10-24',count:2,points:[[9,7,0],[5,5,40]]};
 assert.deepEqual(U.heatPoints(heat),[[9,7,0],[5,5,1]]);
 for(const mutate of [d=>d.points=[],d=>d.points[0][0]=null,d=>d.points[0][2]=-1,d=>d.count=3,d=>d.to='2026-09-30']){const d=copy(heat);mutate(d);assert.throws(()=>U.heatPoints(d))}
});
test('fetch checks HTTP status, revalidates cached JSON and aborts stalled requests',async()=>{
 const seen=[];
 const context=vm.createContext({AbortController,setTimeout,clearTimeout,fetch:async(url,opts)=>{seen.push(opts);return{ok:false,status:404}}});
 vm.runInContext(utilsText,context);await assert.rejects(context.SentimentUtils.fetchJson('data.json'),/404/);assert.equal(seen[0].cache,'no-cache');
 context.fetch=(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted'))));
 await assert.rejects(context.SentimentUtils.fetchJson('data.json',{timeoutMs:5}),/aborted/);
});
test('loaded dashboard renders every state and escapes hostile source text',async()=>{
 const d=copy(data);d.states[0].note='<img src=x onerror=evil()>';d.states[0].party='<script>evil()</script>';d.sources[0].name='<svg onload=evil()>';
 const a=start({stateData:d,x:'fail'});await flush();assert.ifError(a.error);assert.equal(a.layers.length,37);assert.ok(a.node('meta').textContent.includes(data.assessment_updated));
 a.run('openPanel(rec("Abia"))');assert.match(a.node('p-body').innerHTML,/&lt;img/);assert.doesNotMatch(a.node('p-body').innerHTML,/<img/);
 if(rich){assert.match(a.node('p-facts').innerHTML,/&lt;script/);assert.match(a.node('method-body').innerHTML,/&lt;svg/);assert.match(a.node('p-x').textContent,/unavailable/)}
 a.buttons[1].onclick();assert.equal(a.buttons[1].attributes['aria-pressed'],'true');assert.equal(a.buttons[0].attributes['aria-pressed'],'false');
 a.node('close').click();assert.equal(a.node('panel').classList.contains('on'),false);
});
test('invalid data fails visibly without rendering partial state assessments',async()=>{
 const d=copy(data);d.states.pop();const a=start({stateData:d});await flush();assert.equal(a.layers.length,0);assert.match(a.node('meta').textContent,/unavailable or incomplete/);
});
test('missing Leaflet displays a useful error instead of an endless loading message',()=>{
 const a=start({leaflet:false});assert.match(a.node('meta').textContent,/Map library unavailable/);assert.ok(a.error);
});
if(rich){
 test('slow optional X feed does not hold back state controls or map',async()=>{
  const a=start();await flush();assert.equal(a.layers.length,37);assert.equal((a.node('state-jump').innerHTML.match(/<option/g)||[]).length,38);
  assert.equal(a.requests.some(r=>r.url.includes('ped-tool')),false);
  assert.match(a.node('gdelt-meta').textContent,/unavailable/);
  assert.equal(a.requests.filter(r=>r.url.includes('Date.now')||r.url.includes('?ts=')).length,0);
 });
 test('boundary failure leaves all state details usable through the selector',async()=>{
  const a=start({boundary:'fail',x:'fail'});await flush();assert.equal(a.layers.length,0);assert.match(a.node('click-hint').textContent,/Use the state list/);
  a.node('state-jump').onchange({target:{value:'FCT'}});assert.equal(a.node('p-name').textContent,'FCT');assert.equal(a.node('panel').classList.contains('on'),true);
 });
 test('slider bursts schedule one polygon repaint per animation frame',async()=>{
  const a=start({x:'fail'});await flush();a.frames.splice(0).forEach(fn=>fn());
  for(let i=0;i<20;i++)a.node('op-states').oninput({target:{value:String(i)}});
  assert.equal(a.frames.length,1);a.frames[0]();assert.equal(a.layers[0].styles.at(-1).fillOpacity,.19);
 });
 test('X chip labels and glossary preserve punctuation but cannot inject markup',async()=>{
  const xs=JSON.parse(fs.readFileSync(path.join(root,'x_sample.json'),'utf8'));
  const key="A&B' <img src=x>";xs.zones.SE.president=[key];xs.glossary[key]='<script>evil()</script>';
  const a=start({x:xs});await flush();a.run('openPanel(rec("Abia"))');
  assert.match(a.node('p-x').innerHTML,/A&amp;B&#39; &lt;img/);assert.doesNotMatch(a.node('p-x').innerHTML,/<img/);
  assert.match(a.node('chip-def').innerHTML,/&lt;script/);assert.doesNotMatch(a.node('chip-def').innerHTML,/<script/);
 });
 test('current published heat and historical sample pass validation unchanged',()=>{
  const heat=JSON.parse(fs.readFileSync(path.join(root,'gdelt.json'),'utf8'));
  assert.equal(U.heatPoints(heat).length,heat.count);
  const xs=JSON.parse(fs.readFileSync(path.join(root,'x_sample.json'),'utf8'));assert.equal(U.validateX(xs),xs);
  assert.throws(()=>U.validateX({...xs,zones:{SE:{president:'bad'}}}));
 });
}
