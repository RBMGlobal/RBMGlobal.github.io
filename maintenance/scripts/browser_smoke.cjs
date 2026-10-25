/* Public functional checks in fresh browser storage. No account or provider keys. */
'use strict';
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const BASE = (process.env.SITE_BASE_URL || 'https://rbmglobal.co.uk').replace(/\/$/, '');
const OUTPUT = process.env.HEALTH_OUTPUT || 'health-output';
const results = [];
const annotation = text => text.replace(/%/g,'%25').replace(/\r/g,'%0D').replace(/\n/g,'%0A');

(async () => {
  await fs.mkdir(OUTPUT,{recursive:true});
  const browser = await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE_PATH?{executablePath:process.env.BROWSER_EXECUTABLE_PATH}:{})});
  async function check(name, route, exercise) {
    const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const errors = [], network = [];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('requestfailed',r=>network.push({url:r.url().split('?')[0],error:r.failure()?.errorText}));
    try {
      const response = await page.goto(BASE+route,{waitUntil:'domcontentloaded',timeout:60000});
      assert.equal(response.status(),200,'HTTP status');
      await exercise(page);
      assert.deepEqual(errors,[],'Uncaught browser errors');
      results.push({check:name,status:'PASS',detail:'Controls exercised in a fresh browser context',network});
    } catch (error) {
      results.push({check:name,status:'FAIL',detail:error.message,errors,network});
      await page.screenshot({path:path.join(OUTPUT,name+'.png'),fullPage:true}).catch(()=>{});
      console.error('::error::'+annotation(name+': '+error.message));
    } finally { await context.close(); }
  }
  try {
    await check('sentiment','/sentiment/',async page=>{
      await page.waitForFunction(()=>document.querySelector('#state-jump')?.options.length===38);
      await page.selectOption('#state-jump','FCT');
      await page.waitForFunction(()=>document.querySelector('#panel').classList.contains('on') && document.querySelector('#p-name').textContent==='FCT');
      await page.click('button[data-layer="nsa"]');
      assert.equal(await page.getAttribute('button[data-layer="nsa"]','aria-pressed'),'true');
      await page.click('#close');
      await page.waitForFunction(()=>!document.querySelector('#panel').classList.contains('on'));
      await page.waitForFunction(()=>{const e=document.querySelector('#gdelt-meta');return e && /\d{4}-\d{2}-\d{2}/.test(e.textContent) && !/unavailable/i.test(e.textContent)});
      await page.uncheck('#chk-gdelt');assert.equal(await page.isChecked('#chk-gdelt'),false);
      await page.check('#chk-gdelt');assert.equal(await page.isChecked('#chk-gdelt'),true);
      // A functioning selector is a fallback; also verify the map's state geometry loaded.
      await page.waitForFunction(()=>document.querySelectorAll('#map .leaflet-states-pane path').length===37);
    });
    await check('events','/nigeria-activity-map/',async page=>{
      await page.waitForFunction(()=>Number(document.querySelector('#nEvents')?.textContent.replaceAll(',',''))>0 && Number(document.querySelector('#nGdelt')?.textContent.replaceAll(',',''))>0);
      await page.fill('#kw','__rbm_health_no_matching_event__');
      await page.waitForFunction(()=>document.querySelector('#nEvents').textContent==='0' && document.querySelector('#nGdelt').textContent==='0');
      await page.click('#kwClear');
      await page.waitForFunction(()=>Number(document.querySelector('#nEvents').textContent.replaceAll(',',''))>0);
      await page.selectOption('#fState','Borno');assert.equal(await page.inputValue('#fState'),'Borno');
      await page.uncheck('#rwToggle');assert.equal(await page.isChecked('#rwToggle'),false);
      await page.check('#rwToggle');
      assert.equal(await page.locator('#map.leaflet-container').count(),1);
    });
    await check('ped','/ped-tool/beta/',async page=>{
      await page.waitForSelector('#map.leaflet-container');
      const theme=await page.getAttribute('html','data-theme');
      await page.click('#rbmTheme');
      assert.notEqual(await page.getAttribute('html','data-theme'),theme,'Theme changes');
      // Table view exercises an application handler without adding data to any project.
      await page.locator('#tblBtn').evaluate(button=>button.click());
      await page.waitForSelector('#tableView',{state:'visible'});
      await page.locator('#tblBtn').evaluate(button=>button.click());
      await page.waitForSelector('#map',{state:'visible'});
    });
    await check('lz','/lz-tool/beta/',async page=>{
      await page.fill('#inp','not a coordinate');
      await page.waitForFunction(()=>document.querySelector('#perr').textContent.length>0 && document.querySelector('#fixcard').classList.contains('hide'));
      await page.fill('#inp','51.5000, -0.1200');
      await page.waitForSelector('#fixcard:not(.hide)');
      const downloadPromise=page.waitForEvent('download');
      await page.click('#exgpx');
      const download=await downloadPromise;
      const stream=await download.createReadStream();const chunks=[];for await (const chunk of stream)chunks.push(chunk);
      const xml=Buffer.concat(chunks).toString('utf8');
      assert.match(xml,/<gpx\b/);assert.match(xml,/<wpt\b[^>]*lat="51\.5/);assert.match(xml,/lon="-0\.12/);
      await page.fill('#inp','');
      await page.waitForSelector('#fixcard.hide',{state:'attached'});
    });
    for (const [name,route,target] of [['ped-redirect','/ped-tool/','/ped-tool/beta/'],['lz-redirect','/lz-tool/','/lz-tool/beta/'],['sentiment-redirect','/nigeria-sentiment/','/sentiment/']]) {
      await check(name,route,async page=>{await page.waitForURL(BASE+target,{timeout:20000});});
    }
  } finally { await browser.close(); }
  await fs.writeFile(path.join(OUTPUT,'browser-health.json'),JSON.stringify({checked_at:new Date().toISOString(),checks:results},null,2)+'\n');
  const summary='## Browser smoke checks\n\n'+results.map(r=>`- **${r.status}** ${r.check}: ${r.detail.split('\n')[0]}`).join('\n')+'\n';
  if(process.env.GITHUB_STEP_SUMMARY)await fs.appendFile(process.env.GITHUB_STEP_SUMMARY,summary);
  console.log(summary);process.exitCode=results.some(r=>r.status==='FAIL')?1:0;
})().catch(error=>{console.error(error);process.exitCode=1});
