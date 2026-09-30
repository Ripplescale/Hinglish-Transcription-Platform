/** Read-only working-tree, Git index and reachable-history scan. Findings never contain matched text.
 * This is a publication aid, not a complete secret detector. Review flagged files privately.
 * Usage: node scripts/audit-publication.cjs > <private-report-outside-git.json>
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const git = (...args) => execFileSync('git', args, { maxBuffer: 256 * 1024 * 1024 });
const root = git('rev-parse', '--show-toplevel').toString().trim();
process.chdir(root);
const rules = [
  ['token-shaped-value', /\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|hf_[A-Za-z0-9]{25,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b/g],
  ['private-key', /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g],
  ['personal-windows-path', /[A-Za-z]:[\\/]+Users[\\/]+(?!Public(?:[\\/])|<|YOUR_|example(?:[\\/]))[^\s"'<>]+/g],
  ['private-recording-path', /meetily-recordings|Meeting 20\d\d-\d\d-\d\d_\d\d-\d\d/g],
  ['machine-package-cache', /OpenAI\.Codex_[A-Za-z0-9]+|OneDrive - [^\\/\r\n]+[\\/]/g],
];
const account = os.userInfo().username;
if (/^[0-9]{4,}$/.test(account)) rules.push(['local-account-name', new RegExp(`\\b${account}\\b`, 'g')]);
function inspect(buffer, file, scope, object) {
  if (buffer.includes(0)) return [];
  const text = buffer.toString('utf8');
  const found = [];
  for (const [kind, expression] of rules) {
    expression.lastIndex = 0;
    for (const match of text.matchAll(expression)) {
      found.push({ scope, path: file, kind, line: 1 + text.slice(0, match.index).split('\n').length - 1, ...(object ? { object } : {}) });
    }
  }
  return found;
}
const candidates = git('ls-files', '-z', '--cached', '--others', '--exclude-standard').toString().split('\0').filter(Boolean);
const findings = [], binaryFiles = [];
for (const file of candidates) {
  const absolute = path.resolve(root, file);
  if (!fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) continue;
  const bytes = fs.readFileSync(absolute);
  if (bytes.includes(0)) binaryFiles.push({ path: file, size: bytes.length });
  findings.push(...inspect(bytes, file, 'working-tree'));
}
const objects = git('rev-list', '--objects', '--all').toString().trim().split('\n').filter(Boolean).map(line => {
  const at = line.indexOf(' '); return { id: at < 0 ? line : line.slice(0, at), path: at < 0 ? '' : line.slice(at + 1) };
});
function inspectObjects(entries, scope) {
  if (!entries.length) return 0;
  const raw = execFileSync('git', ['cat-file', '--batch'], { input: entries.map(x => x.id).join('\n') + '\n', maxBuffer: 256 * 1024 * 1024 });
  let cursor = 0, count = 0;
  for (const object of entries) {
    const end = raw.indexOf(10, cursor);
    const [, type, sizeText] = raw.subarray(cursor, end).toString().split(' ');
    const size = Number(sizeText);
    if (end < cursor || !Number.isInteger(size) || size < 0) throw new Error('Git returned an unavailable or malformed object. Audit incomplete.');
    cursor = end + 1;
    const bytes = raw.subarray(cursor, cursor + size); cursor += size + 1;
    if (type === 'blob') { count++; findings.push(...inspect(bytes, object.path, scope, object.id)); }
  }
  return count;
}
const historyBlobs = inspectObjects(objects, 'reachable-history');
const index = git('ls-files', '--stage', '-z').toString().split('\0').filter(Boolean).map(entry => {
  const tab = entry.indexOf('\t');
  const [mode, id, stage] = entry.slice(0, tab).split(' ');
  return { mode, id, stage: Number(stage), path: entry.slice(tab + 1) };
}).filter(entry => entry.mode !== '160000'); // Submodule objects live in a separate Git repository.
const indexBlobs = inspectObjects(index, 'git-index');
console.log(JSON.stringify({ version: 2, generated_at: new Date().toISOString(), reachable_commits: Number(git('rev-list', '--all', '--count').toString()), history_blobs_scanned: historyBlobs, index_blobs_scanned: indexBlobs, index_unmerged_paths: [...new Set(index.filter(x => x.stage !== 0).map(x => x.path))], working_files_considered: candidates.length, findings, binary_files: binaryFiles, limitations: ['Heuristic text scan; does not prove absence of credentials or private content.', 'Image, audio and binary contents require provenance or separate review.', 'Ignored files are excluded from current-tree candidates; already tracked/indexed files are always included.', 'The Git index scan reads staged blob contents, which can differ from the working tree.'] }, null, 2));
