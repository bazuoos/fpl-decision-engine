import { readFile, readdir } from "node:fs/promises";
import { extname, join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const forbidden = [
  /fpl_decision_engine/,
  /optimize_xi/,
  /evaluate_one_transfer/,
  /highspy/,
  /duckdb/,
];

async function filesBelow(directory) {
  const output = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) output.push(...(await filesBelow(path)));
    else output.push(path);
  }
  return output;
}

const sourceFiles = (await filesBelow(join(root, "src"))).filter(
  (path) => !path.includes("/test/") && [".ts", ".tsx"].includes(extname(path)),
);
const builtFiles = (await filesBelow(join(root, "dist"))).filter((path) =>
  [".js", ".css", ".html"].includes(extname(path)),
);
const violations = [];
for (const path of [...sourceFiles, ...builtFiles]) {
  const content = await readFile(path, "utf8");
  for (const pattern of forbidden) {
    if (pattern.test(content)) violations.push(`${path}: ${pattern}`);
  }
}
if (violations.length) {
  throw new Error(`trusted-engine implementation leaked into web bundle:\n${violations.join("\n")}`);
}
console.log(`boundary check passed (${sourceFiles.length} source, ${builtFiles.length} built files)`);
