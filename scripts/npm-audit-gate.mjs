import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const advisoryUrl = 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf';
const picomatchVersion = '4.0.7';
const picomatchTarball = 'https://registry.npmjs.org/picomatch/-/picomatch-4.0.7.tgz';
const severities = ['info', 'low', 'moderate', 'high', 'critical'];
const own = (object, key) => Object.hasOwn(object, key);

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

function object(value, label) {
  requireCondition(value !== null && typeof value === 'object' && !Array.isArray(value), `Invalid ${label}: expected object`);
}

function fields(value, expected, label) {
  object(value, label);
  requireCondition(Object.keys(value).length === expected.length && expected.every((key) => own(value, key)), `Unexpected ${label} fields`);
}

function text(value) {
  return typeof value === 'string' && value.length > 0;
}

function strings(value, nonempty = false) {
  return Array.isArray(value) && (!nonempty || value.length > 0) && value.every(text) && new Set(value).size === value.length;
}

function count(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function validateLock(lock) {
  object(lock, 'lockfile');
  requireCondition(lock.lockfileVersion === 3, 'Expected lockfileVersion 3');
  object(lock.packages, 'lockfile packages');
  object(lock.packages[''], 'lockfile root package');
  for (const [path, pkg] of Object.entries(lock.packages)) {
    object(pkg, `lockfile package ${path}`);
    if (/(^|\/)node_modules\/picomatch$/.test(path) || pkg.name === 'picomatch') {
      requireCondition(pkg.version === picomatchVersion && pkg.resolved === picomatchTarball && !pkg.link,
        `Temporary exception requires registry picomatch ${picomatchVersion} at ${path}`);
    }
  }
}

function validateAdvisory(advisory, name) {
  fields(advisory, ['source', 'name', 'dependency', 'title', 'url', 'severity', 'cwe', 'cvss', 'range'], 'advisory');
  requireCondition(Number.isSafeInteger(advisory.source) && advisory.source > 0 && advisory.name === name && advisory.dependency === name
    && text(advisory.title) && text(advisory.url) && text(advisory.range) && severities.includes(advisory.severity)
    && strings(advisory.cwe), `Invalid advisory for ${name}`);
  fields(advisory.cvss, ['score', 'vectorString'], 'CVSS');
  requireCondition(Number.isFinite(advisory.cvss.score) && advisory.cvss.score >= 0 && advisory.cvss.score <= 10
    && (advisory.cvss.vectorString === null || text(advisory.cvss.vectorString)), 'Invalid CVSS values');
  const excepted = name === 'picomatch' && advisory.source === 1247710 && advisory.url === advisoryUrl
    && advisory.severity === 'high' && advisory.range === '<=4.0.7';
  requireCondition(name !== 'picomatch' || excepted, 'Unapproved picomatch advisory');
  return excepted;
}

// Parse the actual npm audit --json report, never its human-readable summary.
// All graph branches must terminate in known advisories; only the exact leaf
// above may justify a high finding. No package-name or dependency-path allowlist.
export function validateAudit(auditText, lockText, status, stderr = '') {
  requireCondition(status === 0 || status === 1, 'Unexpected npm audit exit status');
  requireCondition(stderr === '', 'Unexpected npm audit stderr');
  const report = JSON.parse(auditText);
  const lock = JSON.parse(lockText);
  validateLock(lock);
  fields(report, ['auditReportVersion', 'vulnerabilities', 'metadata'], 'audit report');
  requireCondition(report.auditReportVersion === 2, 'Unsupported npm audit report version');
  object(report.vulnerabilities, 'vulnerabilities');
  fields(report.metadata, ['vulnerabilities', 'dependencies'], 'metadata');
  fields(report.metadata.vulnerabilities, [...severities, 'total'], 'vulnerability counts');
  fields(report.metadata.dependencies, ['prod', 'dev', 'optional', 'peer', 'peerOptional', 'total'], 'dependency counts');
  requireCondition(Object.values(report.metadata.dependencies).every(count), 'Invalid dependency counts');
  const dependencies = report.metadata.dependencies;
  requireCondition(Object.values(dependencies).every((value) => value <= dependencies.total),
    'Dependency class count exceeds total');

  const findings = report.vulnerabilities;
  const totals = Object.fromEntries(severities.map((severity) => [severity, 0]));
  const advisoryExceptions = new Map();
  const vulnerableNodes = new Set();
  for (const [name, finding] of Object.entries(findings)) {
    fields(finding, ['name', 'severity', 'isDirect', 'via', 'effects', 'range', 'nodes', 'fixAvailable'], `finding ${name}`);
    requireCondition(finding.name === name && text(name) && severities.includes(finding.severity)
      && typeof finding.isDirect === 'boolean' && text(finding.range)
      && Array.isArray(finding.via) && finding.via.length > 0
      && strings(finding.effects) && strings(finding.nodes, true), `Invalid finding ${name}`);
    requireCondition(finding.effects.every((effect) => own(findings, effect)), `Unknown effect in ${name}`);
    requireCondition(finding.nodes.every((node) => own(lock.packages, node)
      && (node.endsWith(`/node_modules/${name}`) || node === `node_modules/${name}`
        || lock.packages[node].name === name)), `Unknown lockfile node for ${name}`);
    for (const node of finding.nodes) vulnerableNodes.add(node);
    if (typeof finding.fixAvailable !== 'boolean') {
      fields(finding.fixAvailable, ['name', 'version', 'isSemVerMajor'], 'fixAvailable');
      requireCondition(text(finding.fixAvailable.name) && text(finding.fixAvailable.version)
        && typeof finding.fixAvailable.isSemVerMajor === 'boolean', 'Invalid fixAvailable');
    }
    for (const via of finding.via) {
      if (typeof via === 'string') {
        requireCondition(own(findings, via), `Unknown via reference ${via}`);
      } else {
        advisoryExceptions.set(via, validateAdvisory(via, name));
      }
    }
    totals[finding.severity] += 1;
  }
  requireCondition(dependencies.total >= vulnerableNodes.size,
    'Dependency total is smaller than the unique vulnerable installed nodes');

  const declared = report.metadata.vulnerabilities;
  requireCondition(Object.values(declared).every(count)
    && severities.every((severity) => declared[severity] === totals[severity])
    && declared.total === Object.keys(findings).length, 'Inconsistent vulnerability counts');
  const highCount = totals.high + totals.critical;
  requireCondition(status === (highCount > 0 ? 1 : 0), 'npm audit exit status does not match high/critical findings');

  const visiting = new Set();
  const resolved = new Map();
  function derive(name) {
    requireCondition(!visiting.has(name), `Cyclic via evidence at ${name}`);
    if (resolved.has(name)) return resolved.get(name);
    visiting.add(name);
    const finding = findings[name];
    let onlyException = true;
    let maximumSeverity = -1;
    for (const via of finding.via) {
      const result = typeof via === 'string'
        ? derive(via)
        : { onlyException: advisoryExceptions.get(via), severity: via.severity };
      onlyException = onlyException && result.onlyException;
      maximumSeverity = Math.max(maximumSeverity, severities.indexOf(result.severity));
    }
    requireCondition(finding.severity === severities[maximumSeverity], `Inconsistent severity evidence for ${name}`);
    if (finding.severity === 'high' || finding.severity === 'critical') {
      requireCondition(onlyException, `Unapproved high/critical advisory affecting ${name}`);
    }
    visiting.delete(name);
    const result = { onlyException, severity: finding.severity };
    resolved.set(name, result);
    return result;
  }
  for (const name of Object.keys(findings)) derive(name);
  return { excepted: highCount };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    requireCondition(process.argv.length === 6 && /^[01]$/.test(process.argv[4]),
      'Usage: node npm-audit-gate.mjs AUDIT_JSON LOCKFILE NPM_EXIT_STATUS STDERR_FILE');
    const result = validateAudit(readFileSync(process.argv[2], 'utf8'), readFileSync(process.argv[3], 'utf8'),
      Number(process.argv[4]), readFileSync(process.argv[5], 'utf8'));
    console.log(result.excepted === 0
      ? 'npm audit: no high/critical findings.'
      : `npm audit: ${result.excepted} high findings derive solely from temporary ${advisoryUrl} exception (picomatch ${picomatchVersion}).`);
  } catch (error) {
    console.error(`npm audit gate failed: ${error.message}`);
    process.exitCode = 1;
  }
}
