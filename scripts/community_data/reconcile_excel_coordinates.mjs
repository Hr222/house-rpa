import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import readline from "node:readline";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = "D:/workspace/rongzeyuan/jeethink-rpa";
const workbookPath = `${projectRoot}/outputs/xqdata_excel_20260824/xqData_backup.xlsx`;
const pythonPath = "C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe";
const workerPath = `${projectRoot}/scripts/community_data/reconcile_excel_worker.py`;

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const sheet = workbook.worksheets.getItem("xqData");
const values = sheet.getUsedRange().values;
const headers = values[0].map((value) => String(value ?? ""));
const col = (name) => headers.indexOf(name);
const requiredColumns = [
  "area",
  "name",
  "sync_status",
  "community_id",
  "community_group_id",
  "longitude",
  "latitude",
  "coordinate_system",
  "geocode_status",
  "sync_message",
  "synced_at",
];
for (const name of requiredColumns) {
  if (col(name) < 0) throw new Error(`工作簿缺少必要列: ${name}`);
}

const jobs = [];
for (let row = 1; row < values.length; row += 1) {
  jobs.push({
    row_number: row,
    city: "深圳",
    area: values[row][col("area")],
    name: values[row][col("name")],
  });
}

const python = spawn(pythonPath, ["-X", "utf8", workerPath], {
  cwd: projectRoot,
  env: {
    ...process.env,
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  },
  stdio: ["pipe", "pipe", "pipe"],
});
const errors = [];
python.stderr.on("data", (chunk) => errors.push(String(chunk)));
const exitCodePromise = new Promise((resolve) => python.on("close", resolve));
for (const job of jobs) python.stdin.write(`${JSON.stringify(job)}\n`);
python.stdin.end();

let corrected = 0;
let unresolved = 0;
for await (const line of readline.createInterface({ input: python.stdout })) {
  if (!line.trim()) continue;
  const result = JSON.parse(line);
  const row = Number(result.row_number);
  const current = values[row];
  const next = [
    result.status ?? "PENDING",
    result.community_id ?? null,
    result.community_group_id ?? null,
    result.longitude ?? null,
    result.latitude ?? null,
    result.coordinate_system ?? null,
    result.geocode_status ?? null,
    result.message ?? null,
    new Date().toISOString(),
  ];
  const start = col("sync_status");
  const changed = next.slice(0, 8).some((value, index) => current[start + index] !== value);
  if (changed) {
    sheet.getRangeByIndexes(row, start, 1, next.length).values = [next];
    corrected += 1;
  }
  if (result.status !== "SUCCESS") unresolved += 1;
}
const exitCode = await exitCodePromise;
if (exitCode !== 0) {
  throw new Error(`SQLite 校准工作进程退出码 ${exitCode}: ${errors.join("")}`);
}

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(workbookPath);
console.log(JSON.stringify({ rows: jobs.length, corrected, unresolved, workbookPath }));
