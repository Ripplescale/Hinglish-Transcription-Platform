const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { create, act } = require('react-test-renderer');

function fixture() {
  const state = { ready: false, pending: false, listener: null, starts: 0, errors: [], commands: [] };
  state.start = () => state.starts++;
  const source = fs.readFileSync(path.resolve(__dirname, '../../src/components/MeetingDetectionBridge.tsx'), 'utf8');
  const mocks = {
    '@tauri-apps/api/core': { invoke: async (command, args) => {
      state.commands.push(command);
      if (command === 'meeting_detection_ready') { state.ready = args.ready; return; }
      assert.equal(command, 'meeting_detection_take_request');
      const pending = state.pending; state.pending = false; return pending;
    } },
    '@tauri-apps/api/event': { listen: async (event, listener) => {
      assert.equal(event, 'meeting-record-requested'); state.listener = listener;
      return () => { state.listener = null; };
    } },
    '@/components/Sidebar/SidebarProvider': { useSidebar: () => ({ handleRecordingToggle: state.start }) },
    sonner: { toast: { error: (...args) => state.errors.push(args) } },
  };
  const context = { exports: {}, require: name => mocks[name] || require(name), console };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText, context);
  state.Component = context.exports.MeetingDetectionBridge;
  return state;
}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

test('No detection before setup; explicit request reuses record action exactly once and cleans up', async () => {
  const f = fixture(); let renderer;
  await act(async () => { renderer = create(React.createElement(f.Component, { ready: false })); await flush(); });
  assert.equal(f.listener, null); assert.equal(f.commands.length, 0);
  await act(async () => { renderer.update(React.createElement(f.Component, { ready: true })); await flush(); });
  assert.equal(f.ready, true); assert.equal(f.starts, 0);
  await act(async () => { f.pending = true; await Promise.all([f.listener(), f.listener()]); await flush(); });
  assert.equal(f.starts, 1);
  let updatedStarts = 0; f.start = () => updatedStarts++;
  await act(async () => { renderer.update(React.createElement(f.Component, { ready: true })); await flush(); });
  await act(async () => { f.pending = true; await f.listener(); await flush(); });
  assert.equal(updatedStarts, 1); assert.equal(f.starts, 1);
  await act(async () => { renderer.unmount(); await flush(); });
  assert.equal(f.ready, false); assert.equal(f.listener, null); assert.deepEqual(f.errors, []);
});
test('A confirmed request survives listener setup and is consumed once', async () => {
  const f = fixture(); f.pending = true; let renderer;
  await act(async () => { renderer = create(React.createElement(f.Component, { ready: true })); await flush(); });
  assert.equal(f.starts, 1); assert.equal(f.pending, false);
  await act(async () => { await f.listener(); renderer.unmount(); await flush(); });
  assert.equal(f.starts, 1);
});
