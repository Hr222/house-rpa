import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import readline from "node:readline";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = "D:/workspace/rongzeyuan/jeethink-rpa";
const workbookPath = `${projectRoot}/outputs/xqdata_excel_20260824/xqData_backup.xlsx`;
const pythonPath = "C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe";
const workerPath = `${projectRoot}/scripts/community_data/sync_excel_worker.py`;
const maxConcurrency = 5;
const checkpointEvery = 50;
const outputPath = workbookPath;
const checkpointPath = `${workbookPath}.checkpoint.xlsx`;

const limitArgumentIndex = process.argv.indexOf("--limit");
const requestedLimit = limitArgumentIndex >= 0
  ? Number(process.argv[limitArgumentIndex + 1])
  : null;
if (requestedLimit !== null && (!Number.isInteger(requestedLimit) || requestedLimit <= 0)) {
  throw new Error("--limit 必须是正整数");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const sheet = workbook.worksheets.getItem("xqData");
const used = sheet.getUsedRange();
const values = used.values;
const headers = values[0].map((value) => String(value ?? ""));
const existingSyncStart = headers.indexOf("sync_status");
const syncHeaders = [
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
const syncStart = existingSyncStart >= 0 ? existingSyncStart : headers.length;
const headerIndex = new Map(headers.map((header, index) => [header, index]));
for (const header of syncHeaders) {
  if (!headerIndex.has(header)) {
    headerIndex.set(header, headers.length);
    headers.push(header);
  }
}
if (headers.length > syncStart) {
  sheet.getRangeByIndexes(0, syncStart, 1, headers.length - syncStart).values = [
    headers.slice(syncStart),
  ];
}

const col = (name) => headerIndex.get(name);
const statusCol = col("sync_status");
const statusValues = [];
for (let row = 1; row < values.length; row += 1) {
  const existing = values[row][statusCol];
  statusValues.push([existing === null || existing === undefined || existing === "" ? "PENDING" : existing]);
}
sheet.getRangeByIndexes(1, statusCol, statusValues.length, 1).values = statusValues;
sheet.getRangeByIndexes(0, syncStart, 1, syncHeaders.length).format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
sheet.getRangeByIndexes(1, col("longitude"), values.length - 1, 2).format.numberFormat = "0.000000";
sheet.getRangeByIndexes(0, statusCol, values.length, syncHeaders.length).format.borders = {
  preset: "outside",
  style: "thin",
  color: "#B7C9D6",
};
sheet.getRangeByIndexes(0, statusCol, values.length, 1).format.columnWidth = 14;
sheet.getRangeByIndexes(0, col("community_id"), values.length, 2).format.columnWidth = 16;
sheet.getRangeByIndexes(0, col("longitude"), values.length, 2).format.columnWidth = 14;
sheet.getRangeByIndexes(0, col("coordinate_system"), values.length, 1).format.columnWidth = 16;
sheet.getRangeByIndexes(0, col("geocode_status"), values.length, 1).format.columnWidth = 16;
sheet.getRangeByIndexes(0, col("sync_message"), values.length, 1).format.columnWidth = 28;
sheet.getRangeByIndexes(0, col("synced_at"), values.length, 1).format.columnWidth = 22;

const jobs = [];
for (let row = 1; row < values.length; row += 1) {
  const status = String(sheet.getCell(row, statusCol).values[0][0] ?? "");
  if (status === "SUCCESS") continue;
  const sourceRow = values[row];
  jobs.push({
    row_number: row,
    city: "深圳",
    regionId: sourceRow[col("regionId")],
    area: sourceRow[col("area")],
    district: sourceRow[col("district")],
    name: sourceRow[col("name")],
    rename: sourceRow[col("rename")],
  });
  if (requestedLimit !== null && jobs.length >= requestedLimit) break;
}

const saveCheckpoint = async () => {
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(checkpointPath);
  await fs.copyFile(checkpointPath, outputPath);
  await fs.rm(checkpointPath, { force: true });
};

const python = spawn(pythonPath, ["-X", "utf8", workerPath], {
  cwd: projectRoot,
  env: {
    ...process.env,
    COMMUNITY_SYNC_CONCURRENCY: String(maxConcurrency),
    COMMUNITY_SYNC_QPS: "4",
    COMMUNITY_SYNC_RETRIES: "5",
    COMMUNITY_SYNC_RETRY_DELAY: "1",
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

let processed = 0;
for await (const line of readline.createInterface({ input: python.stdout })) {
  if (!line.trim()) continue;
  const result = JSON.parse(line);
  const row = Number(result.row_number);
  const now = new Date().toISOString();
  const updates = [
    result.status ?? "FAILED",
    result.community_id ?? null,
    result.community_group_id ?? null,
    result.longitude ?? null,
    result.latitude ?? null,
    result.coordinate_system ?? null,
    result.geocode_status ?? null,
    result.message ?? null,
    now,
  ];
  sheet.getRangeByIndexes(row, statusCol, 1, updates.length).values = [updates];
  processed += 1;
  if (processed % checkpointEvery === 0) await saveCheckpoint();
  if (processed % 100 === 0) console.log(`已处理 ${processed}/${jobs.length}`);
}
const exitCode = await exitCodePromise;
await saveCheckpoint();
if (exitCode !== 0) {
  throw new Error(`同步工作进程退出码 ${exitCode}: ${errors.join("")}`);
}

const counts = {};
for (let row = 1; row < values.length; row += 1) {
  const value = String(sheet.getCell(row, statusCol).values[0][0] ?? "");
  counts[value] = (counts[value] ?? 0) + 1;
}
console.log(JSON.stringify({ jobs: jobs.length, processed, counts, workbookPath: outputPath }));
