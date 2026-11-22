// scripts/verify_site_cdp.js
const fs = require('fs');
const path = require('path');

const ARTIFACT_DIR = 'C:\\Users\\Akshay A\\.gemini\\antigravity\\brain\\79fe915e-535a-483c-8c76-79e5941442db';

async function run() {
  const versionResp = await fetch('http://127.0.0.1:9222/json/list');
  const tabs = await versionResp.json();
  const pageTab = tabs.find(t => t.type === 'page') || tabs[0];
  if (!pageTab) {
    console.error('No page tab found on CDP port 9222');
    process.exit(1);
  }

  const ws = new WebSocket(pageTab.webSocketDebuggerUrl);

  let msgId = 1;
  const pending = new Map();

  function send(method, params = {}) {
    const id = msgId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) {
      const { resolve, reject } = pending.get(data.id);
      pending.delete(data.id);
      if (data.error) reject(data.error);
      else resolve(data.result);
    }
  };

  await new Promise((res) => (ws.onopen = res));
  console.log('Connected to CDP');

  await send('Runtime.enable');
  await send('Page.enable');

  await send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false
  });

  // =========================================================================
  // 1. RESULTS PAGE (/results)
  // =========================================================================
  console.log('Navigating to http://127.0.0.1:8000/results ...');
  await send('Page.navigate', { url: 'http://127.0.0.1:8000/results' });
  await new Promise(r => setTimeout(r, 2000));

  const resultsEval = await send('Runtime.evaluate', {
    expression: `(() => {
      return {
        title: document.title,
        headline: document.querySelector('.results-headline')?.innerText?.trim(),
        evidenceCards: Array.from(document.querySelectorAll('.evidence-card')).map(c => ({
          title: c.querySelector('.evidence-card-title')?.textContent?.trim(),
          val: c.querySelector('.stat-val')?.textContent?.trim(),
          unit: c.querySelector('.stat-unit')?.textContent?.trim()
        })),
        bars: {
          uncoordinated: document.getElementById('bar-uncoordinated')?.style?.width,
          arbiter: document.getElementById('bar-arbiter')?.style?.width,
          optimum: document.getElementById('bar-optimum')?.style?.width
        },
        hasAssumptions: !!document.querySelector('.assumptions-box'),
        hasRepro: !!document.querySelector('.info-card')
      };
    })()`,
    returnByValue: true
  });
  console.log('=== RESULTS EVALUATION ===', JSON.stringify(resultsEval.result.value, null, 2));

  const ssResults = await send('Page.captureScreenshot', { format: 'png' });
  const resultsPath = path.join(ARTIFACT_DIR, 'screenshot_results.png');
  fs.writeFileSync(resultsPath, Buffer.from(ssResults.data, 'base64'));
  console.log('Saved results screenshot to:', resultsPath);

  // =========================================================================
  // 2. MONITOR PAGE (/monitor)
  // =========================================================================
  console.log('Navigating to http://127.0.0.1:8000/monitor ...');
  await send('Page.navigate', { url: 'http://127.0.0.1:8000/monitor' });
  await new Promise(r => setTimeout(r, 2000));

  const monitorEval = await send('Runtime.evaluate', {
    expression: `(() => {
      return {
        title: document.title,
        headline: document.querySelector('.monitor-headline')?.innerText?.trim(),
        statusText: document.getElementById('engine-status-text')?.textContent?.trim(),
        statusDesc: document.getElementById('engine-status-desc')?.textContent?.trim(),
        hubCards: Array.from(document.querySelectorAll('.dashboard-hub-card')).map(c => ({
          title: c.querySelector('.card-main-title')?.textContent?.trim(),
          hasImg: !!c.querySelector('.card-thumb-img'),
          links: Array.from(c.querySelectorAll('a')).map(a => a.getAttribute('href'))
        })),
        shortcutsCount: document.querySelectorAll('.shortcut-item').length
      };
    })()`,
    returnByValue: true
  });
  console.log('=== MONITOR EVALUATION ===', JSON.stringify(monitorEval.result.value, null, 2));

  const ssMonitor = await send('Page.captureScreenshot', { format: 'png' });
  const monitorPath = path.join(ARTIFACT_DIR, 'screenshot_monitor.png');
  fs.writeFileSync(monitorPath, Buffer.from(ssMonitor.data, 'base64'));
  console.log('Saved monitor screenshot to:', monitorPath);

  // =========================================================================
  // 3. TEAM PAGE (/team)
  // =========================================================================
  console.log('Navigating to http://127.0.0.1:8000/team ...');
  await send('Page.navigate', { url: 'http://127.0.0.1:8000/team' });
  await new Promise(r => setTimeout(r, 2000));

  const teamEval = await send('Runtime.evaluate', {
    expression: `(() => {
      return {
        title: document.title,
        headline: document.querySelector('.team-headline')?.innerText?.trim(),
        members: Array.from(document.querySelectorAll('.team-member-card')).map(m => ({
          name: m.querySelector('.member-name')?.textContent?.trim(),
          avatar: m.querySelector('.member-avatar')?.textContent?.trim(),
          module: m.querySelector('.member-module-badge')?.textContent?.trim()
        })),
        stackItems: document.querySelectorAll('.stack-item').length,
        repoPlaintext: document.querySelector('.repo-plaintext-url')?.textContent?.trim()
      };
    })()`,
    returnByValue: true
  });
  console.log('=== TEAM EVALUATION ===', JSON.stringify(teamEval.result.value, null, 2));

  const ssTeam = await send('Page.captureScreenshot', { format: 'png' });
  const teamPath = path.join(ARTIFACT_DIR, 'screenshot_team.png');
  fs.writeFileSync(teamPath, Buffer.from(ssTeam.data, 'base64'));
  console.log('Saved team screenshot to:', teamPath);

  ws.close();
  console.log('All verifications completed successfully!');
}

run().catch(err => {
  console.error('Error during site verification:', err);
  process.exit(1);
});
