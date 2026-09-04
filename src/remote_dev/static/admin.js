"use strict";

const $ = (id) => document.getElementById(id);
let csrfToken = "";
let users = [];

function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
}

function toast(message, error = false) {
    const box = $("toast");
    box.textContent = message;
    box.className = `toast show${error ? " error" : ""}`;
    window.setTimeout(() => { box.className = "toast"; }, 3500);
}

async function api(path, options = {}) {
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (csrfToken && !["GET", "HEAD"].includes(options.method || "GET")) {
        headers["X-CSRF-Token"] = csrfToken;
    }
    const response = await fetch(path, {...options, headers, credentials: "same-origin"});
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = {detail: text}; }
    if (!response.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    }
    return data;
}

function userAction(label, handler) {
    const button = el("button", "secondary user-action", label);
    button.type = "button";
    button.addEventListener("click", async () => {
        try { await handler(); } catch (error) { toast(error.message, true); }
    });
    return button;
}

function renderUsers(items) {
    users = items;
    const body = $("users-table");
    const select = $("grant-user");
    body.replaceChildren(); select.replaceChildren();
    for (const user of items) {
        const row = document.createElement("tr");
        const actions = el("td", "user-actions");
        actions.append(
            userAction("改名", async () => {
                const username = window.prompt("新用户名", user.username);
                if (!username || username === user.username) return;
                await api(`/api/admin/users/${user.public_id}`, {
                    method: "PATCH", body: JSON.stringify({username}),
                });
                await Promise.all([loadUsers(), loadAudit()]);
                toast("用户名已更新，工作区 UUID 保持不变");
            }),
            userAction(user.status === "active" ? "禁用" : "启用", async () => {
                const status = user.status === "active" ? "disabled" : "active";
                await api(`/api/admin/users/${user.public_id}`, {
                    method: "PATCH", body: JSON.stringify({status}),
                });
                await Promise.all([loadUsers(), loadAudit()]);
                toast(status === "disabled" ? "用户已禁用" : "用户已启用");
            }),
            userAction("强制退出", async () => {
                const result = await api(`/api/admin/users/${user.public_id}/revoke-sessions`, {
                    method: "POST", body: "{}",
                });
                await loadAudit();
                toast(`已撤销 ${result.revoked_sessions} 个会话`);
            }),
            userAction("轮换 Token", async () => {
                const result = await api(`/api/admin/users/${user.public_id}/rotate-token`, {
                    method: "POST", body: "{}",
                });
                $("token-value").textContent = result.api_token;
                $("token-result").hidden = false;
                await loadAudit();
                toast("Token 已轮换；旧 Token 已立即失效");
            }),
        );
        row.append(
            el("td", "", user.username), el("td", "", user.status),
            el("td", "", user.workspace_path), el("td", "", `${user.workspace_quota_mib} MiB`),
            el("td", "", new Date(user.created_at).toLocaleString()), actions,
        );
        body.appendChild(row);
        const option = document.createElement("option");
        option.value = String(user.public_id); option.textContent = user.username;
        select.appendChild(option);
    }
    $("metric-users").textContent = String(items.length);
}

function renderHeaders(items) {
    const list = $("header-list");
    list.replaceChildren();
    for (const item of items) {
        const button = el("button", `chip${item.enabled ? "" : " off"}`, item.name);
        button.type = "button";
        button.title = item.enabled ? "点击禁用直接引用" : "点击启用直接引用";
        button.addEventListener("click", async () => {
            try {
                await api(`/api/admin/headers/${encodeURIComponent(item.name)}`, {
                    method: "PUT", body: JSON.stringify({enabled: !item.enabled}),
                });
                await loadHeaders();
            } catch (error) { toast(error.message, true); }
        });
        list.appendChild(button);
    }
    $("metric-headers").textContent = String(items.filter(item => item.enabled).length);
}

function renderExtensions(items) {
    const body = $("extensions-table");
    body.replaceChildren();
    for (const item of items) {
        const row = document.createElement("tr");
        row.append(el("td", "", item.extension_id), el("td", "", item.version), el("td", "", item.sha256), el("td", "", item.enabled ? "启用" : "撤回"));
        body.appendChild(row);
    }
}

function renderExtensionRequests(items) {
    const list = $("extension-requests");
    list.replaceChildren();
    for (const item of items) {
        const row = el("article", "audit-row");
        row.append(el("span", "", item.username), el("code", "", `${item.extension_id}@${item.version}`), el("span", "", item.status));
        if (item.status === "pending") {
            const approve = el("button", "", "批准给该用户");
            approve.type = "button";
            approve.addEventListener("click", async () => {
                try {
                    await api(`/api/admin/extension-requests/${item.id}/approve`, {method: "POST", body: JSON.stringify({scope: "user"})});
                    await loadExtensions(); toast("扩展申请已批准");
                } catch (error) { toast(error.message, true); }
            });
            row.appendChild(approve);
        }
        list.appendChild(row);
    }
}

async function loadExtensions() {
    const [catalog, requests] = await Promise.all([api("/api/admin/extensions"), api("/api/admin/extension-requests")]);
    renderExtensions(catalog); renderExtensionRequests(requests);
}

function renderAudit(items) {
    const list = $("audit-list");
    list.replaceChildren();
    for (const item of items) {
        const row = el("article", "audit-row");
        row.append(
            el("time", "", new Date(item.created_at).toLocaleString()),
            el("code", "", item.action),
            el("span", "", `${item.target_type || "system"} ${item.target_id || ""}`),
        );
        list.appendChild(row);
    }
}

async function loadUsers() { renderUsers(await api("/api/admin/users")); }
async function loadHeaders() { renderHeaders(await api("/api/admin/headers")); }
async function loadAudit() { renderAudit(await api("/api/admin/audit")); }
async function refreshAll() { await Promise.all([loadUsers(), loadHeaders(), loadExtensions(), loadAudit()]); }

$("login-form").addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
        const result = await api("/api/admin/login", {
            method: "POST",
            body: JSON.stringify({username: data.get("username"), password: data.get("password")}),
        });
        csrfToken = result.csrf_token;
        $("login-view").hidden = true;
        $("dashboard-view").hidden = false;
        await refreshAll();
    } catch (error) { toast(error.message, true); }
});

$("user-form").addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
        const result = await api("/api/admin/users", {
            method: "POST",
            body: JSON.stringify({username: data.get("username"), password: data.get("password")}),
        });
        $("token-value").textContent = result.api_token;
        $("token-result").hidden = false;
        event.currentTarget.reset();
        await Promise.all([loadUsers(), loadAudit()]);
        toast(`用户 ${result.username} 已创建`);
    } catch (error) { toast(error.message, true); }
});

$("extension-form").addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
        await api("/api/admin/extensions", {
            method: "POST",
            body: JSON.stringify({extension_id: data.get("extension_id"), version: data.get("version"), sha256: data.get("sha256")}),
        });
        event.currentTarget.reset(); await Promise.all([loadExtensions(), loadAudit()]);
        toast("固定版本扩展已加入目录");
    } catch (error) { toast(error.message, true); }
});

$("grant-form").addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const payload = {};
    for (const name of ["memory_limit_mib", "time_limit_ms", "remaining_uses", "expires_in_seconds"]) {
        const value = data.get(name);
        if (value) payload[name] = Number(value);
    }
    try {
        await api(`/api/admin/users/${data.get("user_id")}/grants`, {
            method: "POST", body: JSON.stringify(payload),
        });
        event.currentTarget.reset();
        await loadAudit();
        toast("临时额度已发放");
    } catch (error) { toast(error.message, true); }
});

$("refresh").addEventListener("click", () => refreshAll().then(() => toast("数据已刷新")).catch(error => toast(error.message, true)));
