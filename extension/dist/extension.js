"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/extension.ts
var extension_exports = {};
__export(extension_exports, {
  activate: () => activate,
  deactivate: () => deactivate
});
module.exports = __toCommonJS(extension_exports);
var vscode = __toESM(require("vscode"));

// src/contracts.ts
var ACTIVE_STATES = /* @__PURE__ */ new Set(["queued", "compiling", "running"]);
function validateLimits(timeMs, memoryMiB, caseCount) {
  if (!Number.isFinite(timeMs) || timeMs < 100 || timeMs > 6e4) {
    return "\u65F6\u95F4\u9650\u5236\u5FC5\u987B\u5728 0.1\u201360 \u79D2\u4E4B\u95F4";
  }
  if (!Number.isInteger(memoryMiB) || memoryMiB < 16 || memoryMiB > 256) {
    return "\u5185\u5B58\u9650\u5236\u5FC5\u987B\u5728 16\u2013256 MiB \u4E4B\u95F4";
  }
  if (!Number.isInteger(caseCount) || caseCount < 1 || caseCount > 20) {
    return "\u8F93\u5165\u7EC4\u6570\u5FC5\u987B\u5728 1\u201320 \u4E4B\u95F4";
  }
  return null;
}

// src/credential.ts
var import_node_fs = require("node:fs");
var import_node_path = require("node:path");
function readApiToken(environment = process.env) {
  const directory = environment.CREDENTIALS_DIRECTORY;
  if (!directory) {
    throw new Error("systemd credential directory is unavailable");
  }
  let token;
  try {
    token = (0, import_node_fs.readFileSync)((0, import_node_path.join)(directory, "remote-dev-api-token"), "utf8").trim();
  } catch {
    throw new Error("systemd API token credential is unavailable");
  }
  if (!token) {
    throw new Error("systemd API token credential is empty");
  }
  return token;
}

// src/workspace-policy.ts
var path = __toESM(require("node:path"));
function isWorkspaceCppFile(candidate, roots) {
  if (candidate.scheme !== "file" || !candidate.path.endsWith(".cpp")) {
    return false;
  }
  const candidatePath = path.posix.resolve(candidate.path);
  return roots.some((root) => {
    if (root.scheme !== candidate.scheme) {
      return false;
    }
    const rootPath = path.posix.resolve(root.path);
    const relative = path.posix.relative(rootPath, candidatePath);
    return relative !== "" && relative !== ".." && !relative.startsWith("../") && !path.posix.isAbsolute(relative);
  });
}

// src/extension.ts
var API_BASE = (process.env.REMOTE_DEV_API_BASE || "http://127.0.0.1:9000").replace(/\/$/, "");
async function api(path2, init = {}) {
  const apiToken = readApiToken();
  const response = await fetch(`${API_BASE}${path2}`, {
    ...init,
    headers: {
      "Authorization": `Bearer ${apiToken}`,
      "Content-Type": "application/json",
      ...init.headers || {}
    },
    signal: AbortSignal.timeout(15e3)
  });
  const raw = await response.text();
  let body = {};
  try {
    body = raw ? JSON.parse(raw) : {};
  } catch {
    body = { detail: raw };
  }
  if (!response.ok) {
    const detail = typeof body === "object" && body && "detail" in body ? JSON.stringify(body.detail) : raw;
    throw new Error(`API ${response.status}: ${detail}`);
  }
  return body;
}
async function chooseCppFile() {
  const roots = (vscode.workspace.workspaceFolders || []).map((folder) => folder.uri);
  const active = vscode.window.activeTextEditor?.document.uri;
  const found = (await vscode.workspace.findFiles(
    "**/*.cpp",
    "**/{.git,node_modules}/**",
    500
  )).filter((uri) => isWorkspaceCppFile(uri, roots));
  const ordered = active && isWorkspaceCppFile(active, roots) ? [active, ...found.filter((uri) => uri.toString() !== active.toString())] : found;
  if (!ordered.length) {
    void vscode.window.showWarningMessage("\u5DE5\u4F5C\u533A\u4E2D\u6CA1\u6709 .cpp \u6587\u4EF6");
    return void 0;
  }
  const picked = await vscode.window.showQuickPick(
    ordered.map((uri) => ({
      label: vscode.workspace.asRelativePath(uri, false),
      description: uri.fsPath,
      uri
    })),
    { placeHolder: "\u9009\u62E9\u8981\u63D0\u4EA4\u7684\u5355\u4E2A C++ \u6587\u4EF6", matchOnDescription: false }
  );
  return picked?.uri;
}
function nonce() {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  return Array.from({ length: 32 }, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join("");
}
function webviewHtml(webview, fileLabel) {
  const token = nonce();
  const csp = [
    "default-src 'none'",
    `style-src ${webview.cspSource} 'nonce-${token}'`,
    `script-src 'nonce-${token}'`,
    "img-src data:"
  ].join("; ");
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style nonce="${token}">
:root{color-scheme:dark}body{font:14px/1.5 var(--vscode-font-family);padding:18px;color:var(--vscode-foreground)}
header{margin-bottom:16px}h1{font-size:18px;margin:0 0 4px}.muted{color:var(--vscode-descriptionForeground)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field{display:grid;gap:5px;margin:10px 0}
input,textarea,button{font:inherit}input,textarea{box-sizing:border-box;width:100%;color:var(--vscode-input-foreground);background:var(--vscode-input-background);border:1px solid var(--vscode-input-border);padding:8px;border-radius:5px}
textarea{min-height:120px;font-family:var(--vscode-editor-font-family);resize:vertical}.case{border:1px solid var(--vscode-panel-border);border-radius:7px;padding:10px;margin:10px 0}
.actions{display:flex;gap:8px;position:sticky;bottom:0;background:var(--vscode-sideBar-background);padding:12px 0}
button{border:0;border-radius:5px;padding:7px 12px;color:var(--vscode-button-foreground);background:var(--vscode-button-background);cursor:pointer}button.secondary{background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground)}button:disabled{opacity:.5;cursor:not-allowed}
.result{border-left:3px solid var(--vscode-focusBorder);padding:8px 10px;margin:10px 0;background:var(--vscode-textCodeBlock-background)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto}.status{font-family:var(--vscode-editor-font-family);font-weight:600}.error{color:var(--vscode-errorForeground)}
@media(max-width:560px){.grid{grid-template-columns:1fr}}
</style></head><body>
<header><h1>\u63D0\u4EA4\u5E76\u8FD0\u884C C++17</h1><div class="muted" id="file"></div></header>
<div class="grid"><label class="field">\u65F6\u95F4\u9650\u5236\uFF08\u79D2\uFF09<input id="time" type="number" min="0.1" max="60" step="0.1" value="1"></label><label class="field">\u5185\u5B58\u9650\u5236\uFF08MiB\uFF09<input id="memory" type="number" min="16" max="256" step="1" value="64"></label></div>
<section><div id="cases"></div><button class="secondary" id="add" type="button">\uFF0B \u6DFB\u52A0\u8F93\u5165\u7EC4</button></section>
<div class="actions"><button id="submit" type="button">\u63D0\u4EA4\u8FD0\u884C</button><button class="secondary" id="cancel" type="button" disabled>\u53D6\u6D88\u4F5C\u4E1A</button></div>
<div id="message" class="muted" role="status"></div><section id="results"></section>
<script nonce="${token}">
const vscode=acquireVsCodeApi();const cases=document.getElementById('cases');const results=document.getElementById('results');let count=0;let activeJob=null;
document.getElementById('file').textContent=${JSON.stringify(fileLabel)};
function addCase(value=''){if(count>=20)return;count++;const box=document.createElement('div');box.className='case';const label=document.createElement('label');label.className='field';label.textContent='\u8F93\u5165\u7EC4 '+count;const area=document.createElement('textarea');area.value=value;area.dataset.input='1';area.placeholder='\u4F5C\u4E3A stdin \u4F20\u5165\uFF0C\u53EF\u7559\u7A7A';label.appendChild(area);const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='\u5220\u9664';remove.addEventListener('click',()=>{if(cases.children.length>1){box.remove();renumber();}});box.append(label,remove);cases.appendChild(box);}
function renumber(){count=cases.children.length;[...cases.children].forEach((box,i)=>box.querySelector('label').childNodes[0].textContent='\u8F93\u5165\u7EC4 '+(i+1));}
function setBusy(busy){document.getElementById('submit').disabled=busy;document.getElementById('add').disabled=busy;document.getElementById('cancel').disabled=!busy;}
function text(tag,cls,value){const el=document.createElement(tag);if(cls)el.className=cls;el.textContent=value??'';return el;}
function render(job){results.replaceChildren();results.append(text('h2','','\u4F5C\u4E1A #'+job.id+' \xB7 '+job.status));for(const item of job.cases||[]){const card=document.createElement('article');card.className='result';card.append(text('div','status','\u8F93\u5165\u7EC4 '+item.position+' \xB7 '+item.status));const meta='\u65F6\u95F4 '+(item.wall_time_ms??'-')+' ms \xB7 \u5CF0\u503C\u5185\u5B58 '+(item.peak_memory_kib??'-')+' KiB \xB7 \u9000\u51FA\u7801 '+(item.exit_code??'-')+' \xB7 \u4FE1\u53F7 '+(item.term_signal??'-');card.append(text('div','muted',meta));if(item.stdout)card.append(text('h3','','stdout'),text('pre','',item.stdout));if(item.stderr)card.append(text('h3','','stderr'),text('pre','error',item.stderr));if(item.output_truncated)card.append(text('div','error','\u8F93\u51FA\u5DF2\u8FBE\u5230\u4E0A\u9650\u5E76\u622A\u65AD'));results.append(card);}}
document.getElementById('add').addEventListener('click',()=>addCase());document.getElementById('submit').addEventListener('click',()=>{const inputs=[...document.querySelectorAll('textarea[data-input]')].map(x=>x.value);vscode.postMessage({type:'submit',timeSeconds:Number(document.getElementById('time').value),memoryMiB:Number(document.getElementById('memory').value),inputs});});document.getElementById('cancel').addEventListener('click',()=>{if(activeJob)vscode.postMessage({type:'cancel',jobId:activeJob});});
window.addEventListener('message',event=>{const msg=event.data;document.getElementById('message').textContent=msg.message||'';if(msg.type==='busy'){activeJob=msg.jobId||null;setBusy(msg.value);}if(msg.type==='result')render(msg.job);if(msg.type==='error')document.getElementById('message').className='error';else document.getElementById('message').className='muted';});addCase();
</script></body></html>`;
}
async function pollJob(jobId, panel) {
  const deadline = Date.now() + 24e4;
  while (Date.now() < deadline && panel.visible) {
    const job = await api(`/api/jobs/${jobId}`);
    panel.webview.postMessage({ type: "result", job });
    if (!ACTIVE_STATES.has(job.status)) {
      panel.webview.postMessage({ type: "busy", value: false });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
  throw new Error("\u8F6E\u8BE2\u5DF2\u505C\u6B62\uFF1B\u53EF\u91CD\u65B0\u6253\u5F00\u9762\u677F\u67E5\u8BE2\u4F5C\u4E1A\u5386\u53F2");
}
async function openSubmitPanel() {
  const uri = await chooseCppFile();
  if (!uri) {
    return;
  }
  const fileLabel = vscode.workspace.asRelativePath(uri, false);
  const panel = vscode.window.createWebviewPanel(
    "remoteDevSubmit",
    "\u63D0\u4EA4\u8FD0\u884C",
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  panel.webview.html = webviewHtml(panel.webview, fileLabel);
  panel.webview.onDidReceiveMessage(async (message) => {
    try {
      if (message.type === "submit") {
        const inputs = Array.isArray(message.inputs) ? message.inputs.map(String) : [];
        const timeMs = Math.round(Number(message.timeSeconds) * 1e3);
        const memoryMiB = Number(message.memoryMiB);
        const invalid = validateLimits(timeMs, memoryMiB, inputs.length);
        if (invalid) {
          throw new Error(invalid);
        }
        const roots = (vscode.workspace.workspaceFolders || []).map((folder) => folder.uri);
        if (!isWorkspaceCppFile(uri, roots)) {
          throw new Error("\u6240\u9009\u6587\u4EF6\u4E0D\u518D\u4F4D\u4E8E\u5F53\u524D\u5DE5\u4F5C\u533A\uFF0C\u63D0\u4EA4\u5DF2\u62D2\u7EDD");
        }
        const source = Buffer.from(await vscode.workspace.fs.readFile(uri)).toString("utf8");
        panel.webview.postMessage({ type: "busy", value: true });
        panel.webview.postMessage({ type: "status", message: "\u6B63\u5728\u63D0\u4EA4\u2026" });
        const created = await api("/api/jobs", {
          method: "POST",
          body: JSON.stringify({
            filename: uri.path.split("/").pop(),
            source,
            compiler: "gcc-14-gnu++17",
            time_limit_ms: timeMs,
            memory_limit_mib: memoryMiB,
            inputs
          })
        });
        panel.webview.postMessage({ type: "busy", value: true, jobId: created.id });
        panel.webview.postMessage({ type: "status", message: `\u4F5C\u4E1A #${created.id} \u5DF2\u8FDB\u5165\u961F\u5217` });
        await pollJob(created.id, panel);
      } else if (message.type === "cancel" && Number.isInteger(message.jobId)) {
        await api(`/api/jobs/${message.jobId}/cancel`, { method: "POST", body: "{}" });
        panel.webview.postMessage({ type: "status", message: "\u5DF2\u8BF7\u6C42\u53D6\u6D88" });
      }
    } catch (error) {
      panel.webview.postMessage({ type: "busy", value: false });
      panel.webview.postMessage({ type: "error", message: error instanceof Error ? error.message : String(error) });
    }
  });
}
function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("remoteDev.submit", openSubmitPanel),
    vscode.commands.registerCommand("remoteDev.blocked", () => {
      void vscode.window.showWarningMessage("\u6B64\u529F\u80FD\u5DF2\u88AB\u8FDC\u7A0B\u5F00\u53D1\u5E73\u53F0\u7B56\u7565\u7981\u7528");
    })
  );
}
function deactivate() {
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  activate,
  deactivate
});
