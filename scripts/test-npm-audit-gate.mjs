import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { validateAudit } from './npm-audit-gate.mjs';

function advisory(overrides = {}) {
  return {
    source: 1247710,
    name: 'picomatch',
    dependency: 'picomatch',
    title: 'picomatch vulnerable to ReDoS through single-branch repeated extglobs',
    url: 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf',
    severity: 'high',
    cwe: ['CWE-1333'],
    cvss: { score: 7.5, vectorString: 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H' },
    range: '<=4.0.7',
    ...overrides,
  };
}

function fixture() {
  return {
    report: {
      auditReportVersion: 2,
      vulnerabilities: {
        picomatch: {
          name: 'picomatch', severity: 'high', isDirect: false,
          via: [advisory()], effects: ['parent'], range: '*',
          nodes: ['node_modules/picomatch'], fixAvailable: false,
        },
        parent: {
          name: 'parent', severity: 'high', isDirect: true,
          via: ['picomatch'], effects: [], range: '*',
          nodes: ['node_modules/parent'],
          fixAvailable: { name: 'parent', version: '1.0.0', isSemVerMajor: true },
        },
      },
      metadata: {
        vulnerabilities: { info: 0, low: 0, moderate: 0, high: 2, critical: 0, total: 2 },
        dependencies: { prod: 3, dev: 0, optional: 0, peer: 0, peerOptional: 0, total: 3 },
      },
    },
    lock: {
      name: 'synthetic', version: '1.0.0', lockfileVersion: 3, requires: true,
      packages: {
        '': { name: 'synthetic', version: '1.0.0' },
        'node_modules/picomatch': {
          version: '4.0.7', resolved: 'https://registry.npmjs.org/picomatch/-/picomatch-4.0.7.tgz',
        },
        'node_modules/parent': { version: '2.0.0' },
      },
    },
    status: 1,
    stderr: '',
  };
}

function check({ report, lock, status, stderr }) {
  return validateAudit(JSON.stringify(report), JSON.stringify(lock), status, stderr);
}

function cleanFixture() {
  const input = fixture();
  input.report.vulnerabilities = {};
  input.report.metadata.vulnerabilities = { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 0 };
  input.status = 0;
  return input;
}

test('accepts the exact advisory and recursively derived high findings', () => {
  assert.deepEqual(check(fixture()), { excepted: 2 });
});

test('accepts clean genuine-format reports without bypassing validation', () => {
  assert.deepEqual(check(cleanFixture()), { excepted: 0 });
});

test('follows every branch in a diamond-shaped dependency graph', () => {
  const input = fixture();
  input.report.vulnerabilities.middle = {
    ...structuredClone(input.report.vulnerabilities.parent), name: 'middle',
    nodes: ['node_modules/middle'], effects: ['parent'],
  };
  input.lock.packages['node_modules/middle'] = { version: '1.0.0' };
  input.report.vulnerabilities.parent.via.push('middle');
  input.report.metadata.vulnerabilities.high = 3;
  input.report.metadata.vulnerabilities.total = 3;
  assert.deepEqual(check(input), { excepted: 3 });
});

test('rejects another high advisory reached on a second transitive branch', () => {
  const input = fixture();
  input.report.vulnerabilities.other = {
    ...structuredClone(input.report.vulnerabilities.parent), name: 'other',
    via: [advisory({ name: 'other', dependency: 'other', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc' })],
    nodes: ['node_modules/other'], effects: ['parent'],
  };
  input.lock.packages['node_modules/other'] = { version: '1.0.0' };
  input.report.vulnerabilities.parent.via.push('other');
  input.report.metadata.vulnerabilities.high = 3;
  input.report.metadata.vulnerabilities.total = 3;
  assert.throws(() => check(input));
});

test('accepts multiple installed picomatch instances only at the approved resolution', () => {
  const input = fixture();
  input.lock.packages['node_modules/parent/node_modules/picomatch'] =
    structuredClone(input.lock.packages['node_modules/picomatch']);
  input.report.vulnerabilities.picomatch.nodes.push('node_modules/parent/node_modules/picomatch');
  assert.deepEqual(check(input), { excepted: 2 });
});

test('rejects dependency totals that contradict installed vulnerable nodes', () => {
  const input = fixture();
  for (const key of Object.keys(input.report.metadata.dependencies)) input.report.metadata.dependencies[key] = 0;
  assert.throws(() => check(input));
  input.report.metadata.dependencies.prod = 1;
  input.report.metadata.dependencies.total = 1;
  assert.throws(() => check(input));
});

test('counts unique vulnerable instances rather than only finding names', () => {
  const input = fixture();
  input.lock.packages['node_modules/parent/node_modules/picomatch'] =
    structuredClone(input.lock.packages['node_modules/picomatch']);
  input.report.vulnerabilities.picomatch.nodes.push('node_modules/parent/node_modules/picomatch');
  input.report.metadata.dependencies.prod = 2;
  input.report.metadata.dependencies.total = 2;
  assert.throws(() => check(input));
});

test('rejects individual dependency classes exceeding total even on a clean audit', () => {
  for (const key of ['prod', 'dev', 'optional', 'peer', 'peerOptional']) {
    const input = cleanFixture();
    input.report.metadata.dependencies[key] = 4;
    assert.throws(() => check(input), key);
  }
});

test('allows overlapping npm dependency classes without requiring their sum to equal total', () => {
  const input = fixture();
  input.report.metadata.dependencies = { prod: 3, dev: 3, optional: 3, peer: 3, peerOptional: 3, total: 3 };
  assert.deepEqual(check(input), { excepted: 2 });
});

const rejections = [
  ['unrelated direct high advisory', (i) => { i.report.vulnerabilities.parent.via = [advisory({ name: 'parent', dependency: 'parent', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc' })]; }],
  ['unrelated transitive high advisory', (i) => { i.report.vulnerabilities.picomatch.via[0].url = 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc'; }],
  ['additional picomatch advisory even at low severity', (i) => { i.report.vulnerabilities.picomatch.via.push(advisory({ severity: 'low', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc' })); }],
  ['changed advisory identity', (i) => { i.report.vulnerabilities.picomatch.via[0].source = 999999; }],
  ['critical escalation of the excepted advisory', (i) => { i.report.vulnerabilities.picomatch.via[0].severity = 'critical'; }],
  ['wrong picomatch version', (i) => { i.lock.packages['node_modules/picomatch'].version = '4.0.8'; }],
  ['wrong nested picomatch version', (i) => { i.lock.packages['node_modules/parent/node_modules/picomatch'] = { version: '3.0.1' }; }],
  ['changed resolved tarball', (i) => { i.lock.packages['node_modules/picomatch'].resolved = 'https://example.invalid/picomatch.tgz'; }],
  ['aliased picomatch at another version', (i) => { i.lock.packages['node_modules/alias'] = { name: 'picomatch', version: '4.0.6' }; }],
  ['missing vulnerable node in lock', (i) => { delete i.lock.packages['node_modules/parent']; }],
  ['missing picomatch in lock', (i) => { delete i.lock.packages['node_modules/picomatch']; }],
  ['unexpected lockfile version', (i) => { i.lock.lockfileVersion = 2; }],
  ['npm error accompanying valid findings', (i) => { i.report.error = { code: 'E400', summary: 'Bad request', detail: '' }; }],
  ['unexpected npm exit code', (i) => { i.status = 2; }],
  ['success exit despite high findings', (i) => { i.status = 0; }],
  ['npm error on stderr', (i) => { i.stderr = 'npm error unexpected registry response'; }],
  ['unknown via reference', (i) => { i.report.vulnerabilities.parent.via.push('unknown'); }],
  ['dependency cycle', (i) => { i.report.vulnerabilities.picomatch.via.push('parent'); }],
  ['empty evidence', (i) => { i.report.vulnerabilities.parent.via = []; }],
  ['invalid evidence type', (i) => { i.report.vulnerabilities.parent.via.push(null); }],
  ['hidden high finding in metadata', (i) => { i.report.metadata.vulnerabilities.high = 3; i.report.metadata.vulnerabilities.total = 3; }],
  ['unknown report field', (i) => { i.report.unexpected = true; }],
  ['unknown vulnerability field', (i) => { i.report.vulnerabilities.parent.unexpected = true; }],
  ['unknown advisory field', (i) => { i.report.vulnerabilities.picomatch.via[0].unexpected = true; }],
  ['missing audit metadata', (i) => { delete i.report.metadata; }],
  ['unknown report version', (i) => { i.report.auditReportVersion = 3; }],
  ['invalid severity', (i) => { i.report.vulnerabilities.parent.severity = 'HIGH'; }],
  ['invalid nodes', (i) => { i.report.vulnerabilities.parent.nodes = []; }],
  ['unknown effect reference', (i) => { i.report.vulnerabilities.parent.effects = ['unknown']; }],
];

for (const [name, mutate] of rejections) {
  test(`rejects ${name}`, () => {
    const input = fixture();
    mutate(input);
    assert.throws(() => check(input));
  });
}

test('rejects malformed JSON and malformed clean output', () => {
  const input = cleanFixture();
  assert.throws(() => validateAudit('{', JSON.stringify(input.lock), 0, ''));
  assert.throws(() => validateAudit(JSON.stringify(input.report), '{', 0, ''));
  assert.throws(() => validateAudit('{}', JSON.stringify(input.lock), 0, ''));
  assert.throws(() => validateAudit('null', JSON.stringify(input.lock), 0, ''));
  assert.throws(() => validateAudit('[]', JSON.stringify(input.lock), 0, ''));
  input.report.metadata.vulnerabilities.total = 1;
  assert.throws(() => check(input));
});

test('rejects clean output with a failed npm exit or changed picomatch version', () => {
  const input = cleanFixture();
  input.status = 1;
  assert.throws(() => check(input));
  input.status = 0;
  input.lock.packages['node_modules/picomatch'].version = '4.0.8';
  assert.throws(() => check(input));
});

test('does not lower the existing threshold for unrelated moderate advisories', () => {
  const input = cleanFixture();
  input.report.vulnerabilities.parent = {
    ...fixture().report.vulnerabilities.parent,
    severity: 'moderate',
    via: [advisory({ name: 'parent', dependency: 'parent', severity: 'moderate', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc' })],
  };
  input.report.metadata.vulnerabilities.moderate = 1;
  input.report.metadata.vulnerabilities.total = 1;
  assert.deepEqual(check(input), { excepted: 0 });
});

test('CLI exits nonzero for runtime errors and malformed files, and zero for valid reports', () => {
  const directory = mkdtempSync(join(tmpdir(), 'npm-audit-gate-test-'));
  try {
    const input = fixture();
    const auditPath = join(directory, 'audit.json');
    const lockPath = join(directory, 'lock.json');
    const stderrPath = join(directory, 'stderr.txt');
    writeFileSync(auditPath, JSON.stringify(input.report));
    writeFileSync(lockPath, JSON.stringify(input.lock));
    writeFileSync(stderrPath, '');
    const run = (status) => spawnSync(process.execPath, [
      new URL('./npm-audit-gate.mjs', import.meta.url).pathname,
      auditPath, lockPath, String(status), stderrPath,
    ], { encoding: 'utf8' });
    assert.equal(run(1).status, 0);
    writeFileSync(stderrPath, 'npm error EAI_AGAIN');
    assert.equal(run(1).status, 1);
    writeFileSync(stderrPath, '');
    writeFileSync(auditPath, '{');
    assert.equal(run(1).status, 1);
    assert.equal(readFileSync(lockPath, 'utf8'), JSON.stringify(input.lock));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('CI audits both scopes and retries only transient runtime failures at most three times', () => {
  const workflow = readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
  const match = workflow.match(/          audit_output="\$\(mktemp\)"[\s\S]*?          audit_with_retry --audit-level=high/);
  assert.ok(match, 'CI audit shell block must be present');
  const shell = match[0].split('\n').map((line) => line.slice(10)).join('\n');
  for (const [scenario, expectedExit, expectedCalls] of [
    ['accepted', 0, 2], ['clean', 0, 2], ['transient', 0, 3],
    ['exhausted', 1, 3], ['unexpected', 1, 1], ['malformed', 1, 1],
    ['full-high', 1, 2], ['full-malformed', 1, 2], ['full-runtime', 1, 2],
  ]) {
    const directory = mkdtempSync(join(tmpdir(), 'npm-audit-ci-test-'));
    try {
      const input = scenario === 'clean' ? cleanFixture() : fixture();
      const reportPath = join(directory, 'report.json');
      const callsPath = join(directory, 'calls.jsonl');
      writeFileSync(reportPath, JSON.stringify(input.report));
      const npmPath = join(directory, 'npm');
      writeFileSync(npmPath, `#!${process.execPath}
const { appendFileSync, readFileSync } = require('node:fs');
const calls = process.env.AUDIT_CALLS;
let previous = '';
try { previous = readFileSync(calls, 'utf8'); } catch {}
appendFileSync(calls, JSON.stringify(process.argv.slice(2)) + '\\n');
const attempt = previous.trim() ? previous.trim().split('\\n').length + 1 : 1;
const scenario = process.env.AUDIT_SCENARIO;
const fullScope = !process.argv.includes('--omit=dev');
if (fullScope && scenario === 'full-high') {
  const report = JSON.parse(readFileSync(process.env.AUDIT_REPORT, 'utf8'));
  report.vulnerabilities.parent.via = [{
    ...report.vulnerabilities.picomatch.via[0],
    name: 'parent', dependency: 'parent', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc',
  }];
  console.log(JSON.stringify(report));
  process.exitCode = 1;
} else if (fullScope && scenario === 'full-malformed') {
  console.log('{}');
} else if (fullScope && scenario === 'full-runtime') {
  console.log(JSON.stringify({ error: { code: 'E400', summary: 'unexpected error', detail: '' } }));
  process.exitCode = 2;
} else if (scenario === 'exhausted' || (scenario === 'transient' && attempt === 1)) {
  console.log(JSON.stringify({ error: { code: 'EAI_AGAIN', summary: 'network unavailable', detail: '' } }));
  console.error('npm error EAI_AGAIN');
  process.exitCode = 1;
} else if (scenario === 'unexpected') {
  console.log(JSON.stringify({ error: { code: 'E400', summary: 'unexpected error', detail: '' } }));
  process.exitCode = 2;
} else if (scenario === 'malformed') {
  console.log('{}');
} else {
  process.stdout.write(readFileSync(process.env.AUDIT_REPORT, 'utf8'));
  process.exitCode = scenario === 'clean' ? 0 : 1;
}
`);
      const sleepPath = join(directory, 'sleep');
      writeFileSync(sleepPath, '#!/bin/sh\nexit 0\n');
      chmodSync(npmPath, 0o755);
      chmodSync(sleepPath, 0o755);
      // Keep the actual CI shell and gate, using only synthetic lock data.
      const lockPath = join(directory, 'lock.json');
      writeFileSync(lockPath, JSON.stringify(input.lock));
      const result = spawnSync('bash', ['-e', '-o', 'pipefail', '-c',
        shell.replace('"$audit_output" package-lock.json', '"$audit_output" "$AUDIT_LOCK"')], {
        cwd: new URL('../frontend/', import.meta.url),
        encoding: 'utf8',
        env: {
          ...process.env, PATH: `${directory}:${process.env.PATH}`,
          AUDIT_SCENARIO: scenario, AUDIT_REPORT: reportPath, AUDIT_CALLS: callsPath, AUDIT_LOCK: lockPath,
        },
      });
      assert.equal(result.status, expectedExit, `${scenario}: ${result.stdout}\n${result.stderr}`);
      const calls = readFileSync(callsPath, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
      assert.equal(calls.length, expectedCalls, scenario);
      assert.deepEqual(calls[0], ['audit', '--json', '--omit=dev', '--audit-level=high']);
      if (expectedExit === 0 || scenario.startsWith('full-')) {
        assert.deepEqual(calls.at(-1), ['audit', '--json', '--audit-level=high']);
      }
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  }
});
