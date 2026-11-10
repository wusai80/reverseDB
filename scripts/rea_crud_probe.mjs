// Run only against a fresh disposable replica started by verify_rea_crud.py.
import {readFileSync, writeFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
import assert from "node:assert/strict";

const [sdk, endpoint, harPath, output] = process.argv.slice(2);
const {Client, StreamableHTTPClientTransport} = await import(pathToFileURL(sdk).href);
const entries = JSON.parse(readFileSync(harPath, "utf8")).log.entries;
const seedList = JSON.parse(entries[0].response.content.text);
const client = new Client({name: "reversedb-fresh-crud-check", version: "1"}, {capabilities: {}});
const report = {status: "running", tools: [], calls: [], checks: []};
const query = {ordering: "id", page_size: "100"};
async function call(name, args, status) {
  const result = await client.callTool({name, arguments: args});
  const data = result.structuredContent ?? JSON.parse(result.content.filter(c => c.type === "text").map(c => c.text).join(""));
  report.calls.push({name, args, result});
  assert.equal(data.status, status, JSON.stringify(data));
  return data.body;
}
function check(label, actual, expected) {
  assert.deepEqual(actual, expected, label);
  report.checks.push({label, passed: true});
}
try {
  await client.connect(new StreamableHTTPClientTransport(new URL(endpoint)));
  report.tools = (await client.listTools()).tools;
  check("all five tools available", report.tools.map(t => t.name).sort(),
    ["get_tags", "get_tags_by_id", "post_tags", "patch_tags_by_id", "delete_tags_by_id"].sort());
  for (const name of ["post_tags", "patch_tags_by_id"]) {
    assert.ok(report.tools.find(t => t.name === name).inputSchema.properties.body);
  }
  const initial = await call("get_tags", query, 200);
  check("initial seed state", initial, seedList);
  const created = [];
  for (const name of ["Replica Quartz", "Replica Saffron"]) {
    assert.ok(!JSON.stringify(entries).includes(name), "new name must not occur in training capture");
    const data = await call("post_tags", {body: {name, owner: initial.results[0].owner, color: "#a6cee3"}}, 201);
    assert.ok(![...initial.results, ...created].some(t => t.id === data.id), "ID must be fresh");
    created.push(data);
    const detail = await call("get_tags_by_id", {id: String(data.id)}, 200);
    check("created detail " + name, [detail.id, detail.name, detail.slug, detail.owner, detail.document_count],
      [data.id, name, name.toLowerCase().replaceAll(" ", "-"), initial.results[0].owner, 0]);
    const list = await call("get_tags", query, 200);
    check("create list count " + name, [list.count, list.display_count, list.results.length], Array(3).fill(initial.count + created.length));
    check("create detail/list consistency " + name, list.results.find(t => t.id === data.id), detail);
  }
  for (const tag of created) {
    const name = tag.name + " Revised";
    await call("patch_tags_by_id", {id: String(tag.id), body: {name}}, 200);
    const detail = await call("get_tags_by_id", {id: String(tag.id)}, 200);
    check("renamed detail " + tag.id, [detail.name, detail.slug], [name, name.toLowerCase().replaceAll(" ", "-")]);
    const list = await call("get_tags", query, 200);
    check("rename detail/list consistency " + tag.id, list.results.find(t => t.id === tag.id), detail);
  }
  const page = await call("get_tags", {ordering: "-name", page_size: "1"}, 200);
  const all = await call("get_tags", query, 200);
  check("unrecorded sort and page size", [page.count, page.results.map(t => t.name)],
    [all.count, [all.results.map(t => t.name).sort().reverse()[0]]]);
  for (const [index, tag] of created.entries()) {
    await call("delete_tags_by_id", {id: String(tag.id)}, 204);
    await call("get_tags_by_id", {id: String(tag.id)}, 404);
    const list = await call("get_tags", query, 200);
    check("delete removes object " + tag.id, list.results.some(t => t.id === tag.id), false);
    check("delete counts " + tag.id, [list.count, list.display_count, list.results.length], Array(3).fill(initial.count + created.length - index - 1));
  }
  check("seed objects unchanged after full CRUD", await call("get_tags", query, 200), initial);
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.error = String(error.stack ?? error);
  process.exitCode = 1;
} finally {
  try { await client.close(); }
  finally { writeFileSync(output, JSON.stringify(report, null, 2) + "\n"); }
}
