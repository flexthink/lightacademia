import assert from 'node:assert/strict';
import fs from 'node:fs';
// Run with `node tests/test_workspace_navigation.mjs` (Node 16+).
function test(name, run) {
  run();
  console.log(`PASS ${name}`);
}

const source = fs.readFileSync(new URL('../assets/workspace_navigation.js', import.meta.url), 'utf8');
const {default: render} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function harness(hash, project = 'Alpha', note = 'Home.md') {
  const listeners = new Set();
  const events = [];
  const root = { replaceChildren() {}, appendChild() {} };
  const parent = {
    location: {hash},
    history: {
      pushState(_state, _title, hash) { parent.location.hash = hash; },
      replaceState(_state, _title, hash) { parent.location.hash = hash; },
    },
    addEventListener(_event, callback) { listeners.add(callback); },
    removeEventListener(_event, callback) { listeners.delete(callback); },
  };
  globalThis.window = {parent};
  globalThis.document = {createElement: () => ({appendChild() {}, setAttribute() {}})};
  const component = {
    data: {mode: 'projects', project, note, projects: []},
    parentElement: {querySelector: () => root},
    setTriggerValue: (key, value) => events.push({key, value}),
  };
  const changeHash = (hash) => {
    parent.location.hash = hash;
    for (const callback of [...listeners]) callback();
  };
  return {component, events, parent, listeners, changeHash};
}

test('repeated stale renders do not replay an in-flight project selection', () => {
  const h = harness('#/project/Alpha/note/Home.md');
  render(h.component);
  h.changeHash('#/project/Beta/note/Home.md');
  assert.equal(h.events.length, 1);
  // These renders still carry the old Python selection while the request is in flight.
  for (let i = 0; i < 20; i++) render(h.component);
  assert.equal(h.events.length, 1);
  h.component.data.project = 'Beta';
  render(h.component);
  assert.equal(h.listeners.size, 1);
  h.changeHash('#/project/Alpha/note/Home.md');
  assert.equal(h.events.length, 2);
  assert.equal(h.events[1].value.project, 'Alpha');
});

test('initial deep link reports once even before Python acknowledges it', () => {
  const h = harness('#/project/Beta/note/Other.md');
  for (let i = 0; i < 20; i++) render(h.component);
  assert.deepEqual(h.events, [{key:'route_requested', value:{project:'Beta', note:'Other.md'}}]);
});

test('Python route acknowledgement is silent and back navigation still works', () => {
  const h = harness('#/project/Alpha/note/Home.md');
  render(h.component);
  h.component.data.pushRoute = {project:'.daily-notes', note:'2026-11-24.md'};
  render(h.component);
  // An already queued hashchange must not replay a Python-initiated route either.
  h.changeHash(h.parent.location.hash);
  assert.equal(h.events.length, 0);
  h.component.data = {...h.component.data, project:'.daily-notes', note:'2026-11-24.md', pushRoute:{}};
  render(h.component);
  h.changeHash('#/project/Alpha/note/Home.md');
  assert.equal(h.events.length, 1);
  assert.equal(h.events[0].value.project, 'Alpha');
});
