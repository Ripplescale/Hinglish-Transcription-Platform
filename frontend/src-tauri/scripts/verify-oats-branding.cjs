const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const nativeRoot = path.resolve(__dirname, '..');
const config = JSON.parse(fs.readFileSync(path.join(nativeRoot, 'tauri.conf.json'), 'utf8'));
const windows = JSON.parse(fs.readFileSync(path.join(nativeRoot, 'tauri.windows.conf.json'), 'utf8'));
assert.equal(config.productName, 'oats');
assert.equal(config.app.windows[0].title, 'oats');
assert.equal(config.bundle.publisher, 'oats');
assert.equal(config.identifier, 'com.sttapp.local', 'Keep the saved app identity');
assert.equal(config.mainBinaryName, 'xx', 'Keep the existing executable and launchers compatible');
assert.equal(windows.bundle.windows.nsis.installerIcon, 'icons/app_icon.ico', 'Brand the setup executable');
assert.equal(windows.bundle.windows.nsis.uninstallerIcon, 'icons/app_icon.ico', 'Brand the embedded uninstaller');
assert.match(fs.readFileSync(path.join(nativeRoot, 'Cargo.toml'), 'utf8'), /name = "xx"/);

const cli = JSON.parse(fs.readFileSync(path.join(nativeRoot, '../node_modules/@tauri-apps/cli/package.json'), 'utf8'));
assert.equal(cli.version, '2.11.1', 'Rebase the pinned NSIS template when updating the CLI');
const template = fs.readFileSync(path.join(nativeRoot, windows.bundle.windows.nsis.template), 'utf8').replace(/\r\n/g, '\n');
assert.match(template, /!define UNINSTKEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\xx"/);
assert.match(template, /!define MANUKEY "Software\\xx"/);
assert.match(template, /!define MANUPRODUCTKEY "\$\{MANUKEY\}\\xx"/);
// Prove the vendored template only differs from the matching official release
// by its attribution and the three stable registry identities.
const upstream = template
  .replace(/^[\s\S]*?(?=Unicode true)/, '')
  .replace('; Stable installer identity predates the display-name change. Tauri uses these\n; keys to detect upgrades and restore the existing installation directory.\n', '')
  .replace('!define UNINSTKEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\xx"', '!define UNINSTKEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCTNAME}"')
  .replace('!define MANUKEY "Software\\xx"', '!define MANUKEY "Software\\${MANUFACTURER}"')
  .replace('!define MANUPRODUCTKEY "${MANUKEY}\\xx"', '!define MANUPRODUCTKEY "${MANUKEY}\\${PRODUCTNAME}"');
assert.equal(crypto.createHash('sha256').update(upstream).digest('hex'), 'ee84148e405adc4d736a46456dd8345a644751bd1f28a335dd7fd833a32d7c3e');
const hooks = fs.readFileSync(path.join(nativeRoot, windows.bundle.windows.nsis.installerHooks), 'utf8').replace(/\r\n/g, '\n');
assert.equal((hooks.match(/!insertmacro IsShortcutTarget/g) || []).length, 4);
assert.match(hooks, /\$NoShortcutMode <> 1/);
for (const location of ['$SMPROGRAMS', '$DESKTOP']) {
  const oldShortcut = `${location}\\xx.lnk`;
  const newShortcut = `${location}\\\${PRODUCTNAME}.lnk`;
  assert.ok(hooks.includes(`IsShortcutTarget "${oldShortcut}" "$INSTDIR\\\${MAINBINARYNAME}.exe"`));
  assert.ok(hooks.includes(`Rename "${oldShortcut}" "${newShortcut}"`), 'Preserve the shortcut when /UPDATE does not create the new name');
  assert.ok(hooks.includes(`IsShortcutTarget "${newShortcut}" "$INSTDIR\\\${MAINBINARYNAME}.exe"`), 'Only remove the old shortcut when the new one is ours');
  const guardedRemoval = hooks.slice(hooks.indexOf(`IsShortcutTarget "${newShortcut}"`));
  assert.ok(guardedRemoval.startsWith(`IsShortcutTarget "${newShortcut}" "$INSTDIR\\\${MAINBINARYNAME}.exe"\n        Pop $0\n        \${If} $0 = 1\n          !insertmacro UnpinShortcut "${oldShortcut}"\n          Delete "${oldShortcut}"`));
}
assert.doesNotMatch(hooks, /(?:RmDir|DeleteRegKey|STTApp\\|recordings\\)/i);
console.log('oats display name, stable upgrade identity, pinned NSIS source and guarded shortcut cleanup verified.');
