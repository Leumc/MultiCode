"use strict";

const form = document.getElementById("developer-login-form");
const message = document.getElementById("login-message");
form.addEventListener("submit", async event => {
    event.preventDefault();
    message.textContent = "正在验证…";
    const data = new FormData(form);
    try {
        const response = await fetch("/api/login", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username: data.get("username"), password: data.get("password")}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "登录失败");
        message.textContent = "验证成功，正在进入工作区…";
        window.location.assign(result.workspace_url);
    } catch (error) {
        message.textContent = error instanceof Error ? error.message : String(error);
        message.className = "error";
    }
});
