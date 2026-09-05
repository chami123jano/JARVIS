const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../lanka-projects/lanka-one/node_modules/playwright');
const { spawn } = require('node:child_process');
const { mkdtemp, mkdir, rm } = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'jarvis-browser-'));
  const server = spawn(path.join(root,'.venv/Scripts/python.exe'), ['server.py','--port','4191'], {
    cwd:root, env:{...process.env,JARVIS_DATA_DIR:temporary}, windowsHide:true, stdio:'pipe'
  });
  const closed = new Promise(resolve => server.once('exit',resolve));
  const origin = 'http://127.0.0.1:4191';
  let browser;
  try {
    for(let i=0;i<60;i++) {
      try { if ((await fetch(origin)).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve,100));
    }
    browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror',e => errors.push(e.message));
    await page.goto(origin);
    await page.waitForFunction(() => document.querySelector('#connectionText').textContent !== 'Checking model connection');
    await page.locator('#prompt').fill('calculate (250 + 150) * 2');
    await page.locator('#sendButton').click();
    await page.waitForFunction(() => document.querySelector('.message.assistant p')?.textContent.includes('800'));
    await page.locator('#quickNote').click();
    await page.locator('#recordTitle').fill('Travel plan');
    await page.locator('#recordContent').fill('Compare train times and prepare a packing list.');
    await page.locator('#recordForm [type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#memoryCount').textContent === '1 records');
    await page.locator('[data-view=memory]').first().click();
    assert.match(await page.locator('#recordList').innerText(),/Travel plan/);
    await page.locator('#recordSearch').fill('missing');
    assert.equal(await page.locator('.record').count(),0);
    await page.locator('#recordSearch').fill('');
    await page.locator('[data-view=console]').click();
    await page.locator('#prompt').fill('remind me in 1 second to drink water');
    await page.locator('#sendButton').click();
    await page.waitForSelector('.notification');
    assert.match(await page.locator('.notification').innerText(),/drink water/);
    await page.locator('.notification button').click();
    await page.reload();
    await page.waitForSelector('.message.assistant');
    await page.locator('[data-view=settings]').click();
    await page.locator('#workspacePath').fill(temporary);
    await page.locator('#settingsForm [type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent === 'Settings saved.');
    const out = path.join(root,'test-results');
    await mkdir(out,{recursive:true});
    for (const width of [1440,390]) {
      await page.setViewportSize({width,height:width === 1440 ? 1000 : 844});
      for (const view of ['console','memory','activity','settings']) {
        await page.locator(`[data-view=${view}]`).first().click();
        await page.screenshot({path:path.join(out,`${view}-${width}.png`),fullPage:true});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),false,`${view} overflow at ${width}`);
      }
      await page.locator('[data-view=console]').click();
      const pixels = await page.locator('#reactor').evaluate(canvas => {
        const bytes = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
        let count=0; for(let i=3;i<bytes.length;i+=4) if(bytes[i]>0) count++;
        return count;
      });
      assert.ok(pixels>1000, 'Core canvas is blank');
      const before=await page.locator('#reactor').evaluate(c => c.toDataURL());
      await page.waitForTimeout(120);
      assert.notEqual(await page.locator('#reactor').evaluate(c => c.toDataURL()),before,'Core is not moving');
      await page.locator('#quickReminder').click();
      await page.screenshot({path:path.join(out,`reminder-form-${width}.png`),fullPage:true});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth+1),false);
      await page.locator('[data-close=recordDialog]').click();
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: chat, reminders, persistence, records, settings, responsive views and moving canvas.');
  } finally {
    if(browser) await browser.close();
    server.kill(); await closed;
    await rm(temporary,{recursive:true,force:true});
  }
})().catch(error => {console.error(error);process.exitCode=1;});
