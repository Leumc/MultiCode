import * as vscode from "vscode";
import { ACTIVE_STATES, JobResult, validateLimits } from "./contracts";
import {readApiToken} from "./credential";
import { isWorkspaceCppFile } from "./workspace-policy";

const API_BASE = (process.env.REMOTE_DEV_API_BASE || "http://127.0.0.1:9000").replace(/\/$/, "");
async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const apiToken = readApiToken();
    const response = await fetch(`${API_BASE}${path}`, {
        ...init,
        headers: {
            "Authorization": `Bearer ${apiToken}`,
            "Content-Type": "application/json",
            ...(init.headers || {}),
        },
        signal: AbortSignal.timeout(15_000),
    });
    const raw = await response.text();
    let body: unknown = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch { body = { detail: raw }; }
    if (!response.ok) {
        const detail = typeof body === "object" && body && "detail" in body
            ? JSON.stringify((body as {detail: unknown}).detail)
            : raw;
        throw new Error(`API ${response.status}: ${detail}`);
    }
    return body as T;
}

async function chooseCppFile(): Promise<vscode.Uri | undefined> {
    const roots = (vscode.workspace.workspaceFolders || []).map(folder => folder.uri);
    const active = vscode.window.activeTextEditor?.document.uri;
    const found = (await vscode.workspace.findFiles(
        "**/*.cpp", "**/{.git,node_modules}/**", 500
    )).filter(uri => isWorkspaceCppFile(uri, roots));
    const ordered = active && isWorkspaceCppFile(active, roots)
        ? [active, ...found.filter(uri => uri.toString() !== active.toString())]
        : found;
    if (!ordered.length) {
        void vscode.window.showWarningMessage("工作区中没有 .cpp 文件");
        return undefined;
    }
    const picked = await vscode.window.showQuickPick(
        ordered.map(uri => ({
            label: vscode.workspace.asRelativePath(uri, false),
            description: uri.fsPath,
            uri,
        })),
        {placeHolder: "选择要提交的单个 C++ 文件", matchOnDescription: false},
    );
    return picked?.uri;
}

function nonce(): string {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    return Array.from({length: 32}, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join("");
}

function webviewHtml(webview: vscode.Webview, fileLabel: string): string {
    const token = nonce();
    const csp = [
        "default-src 'none'",
        `style-src ${webview.cspSource} 'nonce-${token}'`,
        `script-src 'nonce-${token}'`,
        "img-src data:",
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
<header><h1>提交并运行 C++17</h1><div class="muted" id="file"></div></header>
<div class="grid"><label class="field">时间限制（秒）<input id="time" type="number" min="0.1" max="60" step="0.1" value="1"></label><label class="field">内存限制（MiB）<input id="memory" type="number" min="16" max="256" step="1" value="64"></label></div>
<section><div id="cases"></div><button class="secondary" id="add" type="button">＋ 添加输入组</button></section>
<div class="actions"><button id="submit" type="button">提交运行</button><button class="secondary" id="cancel" type="button" disabled>取消作业</button></div>
<div id="message" class="muted" role="status"></div><section id="results"></section>
<script nonce="${token}">
const vscode=acquireVsCodeApi();const cases=document.getElementById('cases');const results=document.getElementById('results');let count=0;let activeJob=null;
document.getElementById('file').textContent=${JSON.stringify(fileLabel)};
function addCase(value=''){if(count>=20)return;count++;const box=document.createElement('div');box.className='case';const label=document.createElement('label');label.className='field';label.textContent='输入组 '+count;const area=document.createElement('textarea');area.value=value;area.dataset.input='1';area.placeholder='作为 stdin 传入，可留空';label.appendChild(area);const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='删除';remove.addEventListener('click',()=>{if(cases.children.length>1){box.remove();renumber();}});box.append(label,remove);cases.appendChild(box);}
function renumber(){count=cases.children.length;[...cases.children].forEach((box,i)=>box.querySelector('label').childNodes[0].textContent='输入组 '+(i+1));}
function setBusy(busy){document.getElementById('submit').disabled=busy;document.getElementById('add').disabled=busy;document.getElementById('cancel').disabled=!busy;}
function text(tag,cls,value){const el=document.createElement(tag);if(cls)el.className=cls;el.textContent=value??'';return el;}
function render(job){results.replaceChildren();results.append(text('h2','','作业 #'+job.id+' · '+job.status));for(const item of job.cases||[]){const card=document.createElement('article');card.className='result';card.append(text('div','status','输入组 '+item.position+' · '+item.status));const meta='时间 '+(item.wall_time_ms??'-')+' ms · 峰值内存 '+(item.peak_memory_kib??'-')+' KiB · 退出码 '+(item.exit_code??'-')+' · 信号 '+(item.term_signal??'-');card.append(text('div','muted',meta));if(item.stdout)card.append(text('h3','','stdout'),text('pre','',item.stdout));if(item.stderr)card.append(text('h3','','stderr'),text('pre','error',item.stderr));if(item.output_truncated)card.append(text('div','error','输出已达到上限并截断'));results.append(card);}}
document.getElementById('add').addEventListener('click',()=>addCase());document.getElementById('submit').addEventListener('click',()=>{const inputs=[...document.querySelectorAll('textarea[data-input]')].map(x=>x.value);vscode.postMessage({type:'submit',timeSeconds:Number(document.getElementById('time').value),memoryMiB:Number(document.getElementById('memory').value),inputs});});document.getElementById('cancel').addEventListener('click',()=>{if(activeJob)vscode.postMessage({type:'cancel',jobId:activeJob});});
window.addEventListener('message',event=>{const msg=event.data;document.getElementById('message').textContent=msg.message||'';if(msg.type==='busy'){activeJob=msg.jobId||null;setBusy(msg.value);}if(msg.type==='result')render(msg.job);if(msg.type==='error')document.getElementById('message').className='error';else document.getElementById('message').className='muted';});addCase();
</script></body></html>`;
}

async function pollJob(jobId: number, panel: vscode.WebviewPanel): Promise<void> {
    const deadline = Date.now() + 240_000;
    while (Date.now() < deadline && panel.visible) {
        const job = await api<JobResult>(`/api/jobs/${jobId}`);
        panel.webview.postMessage({type: "result", job});
        if (!ACTIVE_STATES.has(job.status)) {
            panel.webview.postMessage({type: "busy", value: false});
            return;
        }
        await new Promise(resolve => setTimeout(resolve, 800));
    }
    throw new Error("轮询已停止；可重新打开面板查询作业历史");
}

async function openSubmitPanel(): Promise<void> {
    const uri = await chooseCppFile();
    if (!uri) { return; }
    const fileLabel = vscode.workspace.asRelativePath(uri, false);
    const panel = vscode.window.createWebviewPanel(
        "remoteDevSubmit", "提交运行", vscode.ViewColumn.Beside,
        {enableScripts: true, retainContextWhenHidden: true},
    );
    panel.webview.html = webviewHtml(panel.webview, fileLabel);
    panel.webview.onDidReceiveMessage(async message => {
        try {
            if (message.type === "submit") {
                const inputs = Array.isArray(message.inputs) ? message.inputs.map(String) : [];
                const timeMs = Math.round(Number(message.timeSeconds) * 1000);
                const memoryMiB = Number(message.memoryMiB);
                const invalid = validateLimits(timeMs, memoryMiB, inputs.length);
                if (invalid) { throw new Error(invalid); }
                const roots = (vscode.workspace.workspaceFolders || []).map(folder => folder.uri);
                if (!isWorkspaceCppFile(uri, roots)) {
                    throw new Error("所选文件不再位于当前工作区，提交已拒绝");
                }
                const source = Buffer.from(await vscode.workspace.fs.readFile(uri)).toString("utf8");
                panel.webview.postMessage({type: "busy", value: true});
                panel.webview.postMessage({type: "status", message: "正在提交…"});
                const created = await api<{id: number}>("/api/jobs", {
                    method: "POST",
                    body: JSON.stringify({
                        filename: uri.path.split("/").pop(), source,
                        compiler: "gcc-14-gnu++17", time_limit_ms: timeMs,
                        memory_limit_mib: memoryMiB, inputs,
                    }),
                });
                panel.webview.postMessage({type: "busy", value: true, jobId: created.id});
                panel.webview.postMessage({type: "status", message: `作业 #${created.id} 已进入队列`});
                await pollJob(created.id, panel);
            } else if (message.type === "cancel" && Number.isInteger(message.jobId)) {
                await api(`/api/jobs/${message.jobId}/cancel`, {method: "POST", body: "{}"});
                panel.webview.postMessage({type: "status", message: "已请求取消"});
            }
        } catch (error) {
            panel.webview.postMessage({type: "busy", value: false});
            panel.webview.postMessage({type: "error", message: error instanceof Error ? error.message : String(error)});
        }
    });
}

export function activate(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
        vscode.commands.registerCommand("remoteDev.submit", openSubmitPanel),
        vscode.commands.registerCommand("remoteDev.blocked", () => {
            void vscode.window.showWarningMessage("此功能已被远程开发平台策略禁用");
        }),
    );
}

export function deactivate(): void {}
