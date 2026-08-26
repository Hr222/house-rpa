import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = "D:/workspace/rongzeyuan/jeethink-rpa";
const defaultWorkbookPath = `${projectRoot}/outputs/xqdata_excel_20260824/xqData_backup.xlsx`;
const defaultResultsPath = `${projectRoot}/outputs/xqdata_excel_20260824/build_year_backfill.jsonl`;

function optionValue(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length ? process.argv[index + 1] : fallback;
}

const workbookPath = optionValue("--workbook", defaultWorkbookPath);
const resultsPath = optionValue("--results", defaultResultsPath);
const outputPath = optionValue("--output", workbookPath);

function latestResultsByCommunityId(lines) {
  const results = new Map();
  for (const line of lines.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const item = JSON.parse(line);
    const communityId = Number(item.community_id);
    if (Number.isInteger(communityId) && communityId > 0) results.set(communityId, item);
  }
  return results;
}

function serialise(value) {
  if (value === undefined || value === null) return null;
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

const resultText = await fs.readFile(resultsPath, "utf8");
const resultsById = latestResultsByCommunityId(resultText);
if (resultsById.size === 0) throw new Error(`没有可写入的年份结果: ${resultsPath}`);

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const sheet = workbook.worksheets.getItem("xqData");
const used = sheet.getUsedRange();
const values = used.values;
const headers = values[0].map((value) => String(value ?? ""));
const columns = [
  "build_year",
  "build_year_status",
  "build_year_rule",
  "build_year_evidence",
  "build_year_source_urls",
  "build_year_updated_at",
];
const headerIndex = new Map(headers.map((header, index) => [header, index]));
const firstBuildYearColumn = headerIndex.has("build_year") ? headerIndex.get("build_year") : headers.length;
for (const column of columns) {
  if (!headerIndex.has(column)) {
    headerIndex.set(column, headers.length);
    headers.push(column);
  }
}
sheet.getRangeByIndexes(0, firstBuildYearColumn, 1, headers.length - firstBuildYearColumn).values = [
  headers.slice(firstBuildYearColumn),
];

const col = (name) => headerIndex.get(name);
const idCol = col("community_id");
if (idCol === undefined) throw new Error("工作簿缺少 community_id 列，无法安全回写年份");
const writeStart = col("build_year");
let updated = 0;
for (let row = 1; row < values.length; row += 1) {
  const communityId = Number(values[row][idCol]);
  const result = resultsById.get(communityId);
  if (!result) {
    // 早期人工回填使用 CONFIRMED；统一为当前对外状态 SUCCESS。
    const existingYear = Number(values[row][col("build_year")]);
    const existingStatus = String(values[row][col("build_year_status")] ?? "");
    if (Number.isInteger(existingYear) && existingStatus && existingStatus !== "SUCCESS") {
      sheet.getCell(row, col("build_year_status")).values = [["SUCCESS"]];
    }
    continue;
  }
  const rowValues = [
    result.status === "SUCCESS" ? result.build_year : null,
    serialise(result.status),
    serialise(result.rule),
    serialise(result.evidence || result.message),
    serialise(result.source_urls),
    serialise(result.checked_at),
  ];
  sheet.getRangeByIndexes(row, writeStart, 1, rowValues.length).values = [rowValues];
  updated += 1;
}

sheet.getRangeByIndexes(0, writeStart, 1, columns.length).format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
sheet.getRangeByIndexes(1, col("build_year"), values.length - 1, 1).format.numberFormat = "0";
sheet.getRangeByIndexes(0, writeStart, values.length, columns.length).format.borders = {
  preset: "outside",
  style: "thin",
  color: "#B7C9D6",
};
sheet.getRangeByIndexes(0, col("build_year"), values.length, 1).format.columnWidth = 12;
sheet.getRangeByIndexes(0, col("build_year_status"), values.length, 1).format.columnWidth = 18;
sheet.getRangeByIndexes(0, col("build_year_rule"), values.length, 1).format.columnWidth = 35;
sheet.getRangeByIndexes(0, col("build_year_evidence"), values.length, 1).format.columnWidth = 72;
sheet.getRangeByIndexes(0, col("build_year_source_urls"), values.length, 1).format.columnWidth = 52;
sheet.getRangeByIndexes(0, col("build_year_updated_at"), values.length, 1).format.columnWidth = 24;
sheet.getRangeByIndexes(1, col("build_year_updated_at"), values.length - 1, 1).format.numberFormat = "yyyy-mm-dd hh:mm";

const statusRange = sheet.getRangeByIndexes(1, col("build_year_status"), values.length - 1, 1);
statusRange.conditionalFormats.add("containsText", {
  text: "SUCCESS",
  format: { fill: "#E2F0D9", font: { color: "#375623" } },
});
statusRange.conditionalFormats.add("containsText", {
  text: "MANUAL_REVIEW",
  format: { fill: "#FCE4D6", font: { color: "#C00000" } },
});
sheet.freezePanes.freezeRows(1);

const inspection = await workbook.inspect({
  kind: "table",
  range: `xqData!${String.fromCharCode(65 + writeStart)}1:${String.fromCharCode(65 + Math.min(writeStart + columns.length - 1, 25))}8`,
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: columns.length,
});
console.log(inspection.ndjson);

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 20 },
  summary: "formula error scan",
});
console.log(formulaErrors.ndjson);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
console.log(JSON.stringify({ updated, outputPath, resultsPath }));
