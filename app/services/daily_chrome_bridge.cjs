/* Connect through the Playwright extension shipped by Microsoft. The pinned
 * Playwright driver supplies the relay and protocol; no copied browser profile.
 * See docs/DAILY_CHROME.md for the integration and permission boundary. */
'use strict';
const path = require('path');
const crypto = require('crypto');
const core = require(path.join(process.argv[2], 'lib', 'coreBundle.js'));
let browser, server, closing = false;
const status = value => process.stdout.write(JSON.stringify(value) + '\n');
async function stop() {
  if (closing) return;
  closing = true;
  const deadline = setTimeout(() => process.exit(0), 2000);
  deadline.unref();
  try {
    // For an extension connection this detaches; Chrome belongs to the user.
    if (server) await server.close();
    else if (browser) await browser.close();
  } finally { process.exit(0); }
}
process.stdin.resume();
process.stdin.on('end', stop);
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
(async () => {
  status({state: 'waiting'});
  const connected = await core.tools.createBrowserWithInfo({
    extension: true,
    browser: {browserName: 'chromium', launchOptions: {}, contextOptions: {}},
  }, {clientName: 'iPhone AutoBuyer'}, {browser: 'chrome'});
  browser = connected.browser;
  if (connected.ownership !== 'attached') throw new Error('unexpected ownership');
  const impl = browser._connection.toImpl(browser);
  server = new core.remote.PlaywrightServer({
    // Shared mode exposes the already-connected default context. Ordinary
    // launchServer intentionally hides it and would lose the user's session.
    mode: 'launchServerShared', path: '/' + crypto.randomUUID(),
    maxConnections: 1, preLaunchedBrowser: impl,
  });
  const endpoint = await server.listen(0, '127.0.0.1');
  status({state: 'connected', endpoint});
  browser.on('disconnected', stop);
})().catch(async () => {
  // Upstream error strings can include connection tokens and profile paths.
  status({state: 'failed'});
  await stop();
});
