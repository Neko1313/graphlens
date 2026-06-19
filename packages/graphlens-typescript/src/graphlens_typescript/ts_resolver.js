"use strict";
const path = require("path");

const cacheDir = process.env.TS_CACHE_DIR;
let ts;
try {
  ts = require(require.resolve("typescript", { paths: [cacheDir] }));
} catch (e) {
  process.stdout.write(JSON.stringify({ results: [], error: "no-typescript" }));
  process.exit(0);
}

function loadConfig(projectRoot) {
  const configPath = ts.findConfigFile(projectRoot, ts.sys.fileExists, "tsconfig.json");
  if (!configPath) {
    const files = ts.sys.readDirectory(projectRoot, [".ts", ".tsx", ".mts", ".cts"]);
    return {
      options: { target: ts.ScriptTarget.ES2020, allowJs: true, skipLibCheck: true },
      fileNames: files,
    };
  }
  const { config, error } = ts.readConfigFile(configPath, ts.sys.readFile);
  if (error) return { options: { skipLibCheck: true }, fileNames: [] };
  const parsed = ts.parseJsonConfigFileContent(
    config, ts.sys, path.dirname(configPath), undefined, configPath);
  parsed.options.skipLibCheck = true;
  return { options: parsed.options, fileNames: parsed.fileNames };
}

function buildService(projectRoot) {
  const { options, fileNames } = loadConfig(projectRoot);
  const host = {
    getScriptFileNames: () => fileNames,
    getScriptVersion: () => "0",
    getScriptSnapshot: (f) =>
      ts.sys.fileExists(f) ? ts.ScriptSnapshot.fromString(ts.sys.readFile(f) || "") : undefined,
    getCurrentDirectory: () => projectRoot,
    getCompilationSettings: () => options,
    getDefaultLibFileName: (o) => ts.getDefaultLibFilePath(o),
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
  };
  return ts.createLanguageService(host, ts.createDocumentRegistry());
}

function classifyOrigin(fileName, projectRoot) {
  if (!fileName) return "unknown";
  const n = fileName.replace(/\\/g, "/");
  if (/\/typescript\/lib\/lib\.[^/]+\.d\.ts$/.test(n)) return "stdlib";
  if (n.includes("/node_modules/")) return "third_party";
  const root = path.resolve(projectRoot).replace(/\\/g, "/");
  if (n.startsWith(root + "/")) return "internal";
  return "unknown";
}

function answer(service, projectRoot, q) {
  try {
    const program = service.getProgram();
    if (!program) return null;
    const sf = program.getSourceFile(q.file);
    if (!sf) return null;
    const offset = ts.getPositionOfLineAndCharacter(sf, q.line - 1, q.col - 1);
    const defs = service.getDefinitionAtPosition(q.file, offset);
    if (!defs || defs.length === 0) return null;
    const d = defs[0];
    const dsf = program.getSourceFile(d.fileName);
    const lc = dsf ? dsf.getLineAndCharacterOfPosition(d.textSpan.start) : { line: 0, character: 0 };
    return {
      file: d.fileName, line: lc.line + 1, col: lc.character + 1,
      name: d.name, kind: d.kind,
      origin: classifyOrigin(d.fileName, projectRoot),
    };
  } catch (e) { return null; }
}

async function main() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  let request;
  try { request = JSON.parse(Buffer.concat(chunks).toString()); }
  catch (e) { process.stdout.write(JSON.stringify({ results: [] })); return; }
  const root = path.resolve(request.project_root);
  let service;
  try { service = buildService(root); }
  catch (e) { process.stdout.write(JSON.stringify({ results: request.queries.map(() => null) })); return; }
  const results = request.queries.map((q) =>
    answer(service, root, { ...q, file: path.resolve(root, q.file) }));
  process.stdout.write(JSON.stringify({ results }));
}
main().catch(() => process.stdout.write(JSON.stringify({ results: [] })));
