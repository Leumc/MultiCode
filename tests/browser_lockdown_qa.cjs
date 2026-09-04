"use strict";

const fs = require("node:fs");
const { chromium } = require("playwright");

(async () => {
    const executablePath = process.env.CHROMIUM_EXECUTABLE;
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const consoleErrors = [];
    const pageErrors = [];
    const externalRequests = [];
    page.on("console", message => {
        if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", error => pageErrors.push(String(error)));
    page.on("request", request => {
        const url = new URL(request.url());
        if (["http:", "https:"].includes(url.protocol)
            && !['127.0.0.1', 'localhost'].includes(url.hostname)) {
            externalRequests.push(request.url());
        }
    });

    await page.goto("http://127.0.0.1:9199/", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await page.locator(".monaco-workbench").waitFor({ timeout: 60_000 });
    await page.waitForTimeout(4_000);

    await page.keyboard.press("Control+P");
    const quickWidget = page.locator(".quick-input-widget");
    await quickWidget.waitFor({ state: "visible", timeout: 10_000 });
    const quickInput = page.locator(".quick-input-widget input:visible");
    await quickInput.waitFor({ state: "visible", timeout: 10_000 });
    await quickInput.fill("main.cpp");
    await page.waitForTimeout(1_000);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2_000);
    const mainCppOpened = (await page.title()).includes("main.cpp")
        || (await page.locator("body").innerText()).includes("main.cpp");

    const terminalSelector = ".terminal-instance, .xterm";
    const terminalsBefore = await page.locator(terminalSelector).count();
    await page.keyboard.press("Control+Backquote");
    await page.waitForTimeout(1_500);
    const terminalsAfter = await page.locator(terminalSelector).count();

    async function openCommandPalette(query) {
        await page.locator("li.command-center-quick-pick[role=button]").evaluate(element => element.click());
        await page.locator(".quick-input-widget input:visible").waitFor({ timeout: 10_000 });
        await quickInput.fill(`>${query}`);
    }

    await openCommandPalette("Remote Dev");
    await page.waitForTimeout(1_500);
    const extensionCommands = await page.locator(".quick-input-list .monaco-list-row").allInnerTexts();
    await page.keyboard.press("Escape");

    const forbiddenQueries = [
        "Terminal: Create New Terminal",
        "Tasks: Run Task",
        "Debug: Start Debugging",
        "Ports: Forward a Port",
    ];
    const forbiddenCommandVisibility = {};
    for (const query of forbiddenQueries) {
        await openCommandPalette(query);
        await page.waitForTimeout(800);
        forbiddenCommandVisibility[query] = await page.locator(".quick-input-list .monaco-list-row").allInnerTexts();
        await page.keyboard.press("Escape");
    }

    const directTerminalBefore = await page.locator(terminalSelector).count();
    await openCommandPalette("Terminal: Create New Terminal");
    await page.waitForTimeout(800);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2_000);
    const directTerminalAfter = await page.locator(terminalSelector).count();
    await page.keyboard.press("Escape");

    await page.keyboard.press("F5");
    await page.keyboard.press("Control+Shift+B");
    await page.waitForTimeout(1_000);

    const result = {
        url: page.url(),
        title: await page.title(),
        workbench: await page.locator(".monaco-workbench").count() === 1,
        mainCppOpened,
        mainCppVisibleAtEnd: (await page.locator("body").innerText()).includes("main.cpp"),
        extensionCommands,
        terminalsBefore,
        terminalsAfter,
        directTerminalBefore,
        directTerminalAfter,
        debugToolbarVisible: await page.locator(".debug-toolbar").isVisible().catch(() => false),
        restrictedDebugViewVisible: (await page.locator("body").innerText()).includes("RUN AND DEBUG: RUN"),
        chatVisible: (await page.locator("body").innerText()).includes("Build with Agent"),
        taskQuickInputVisible: await quickInput.isVisible().catch(() => false),
        forbiddenCommandVisibility,
        externalRequests: [...new Set(externalRequests)],
        consoleErrors,
        pageErrors,
    };
    await page.screenshot({ path: ".dev/browser-qa/workbench.png", fullPage: true });
    fs.writeFileSync(".dev/browser-qa/result.json", JSON.stringify(result, null, 2));
    await browser.close();

    if (!result.workbench || !result.mainCppOpened) throw new Error("workbench or main.cpp did not render");
    if (!extensionCommands.some(value => value.includes("Remote Dev: 提交并运行 C++"))) {
        throw new Error("controlled submission command was not registered");
    }
    if (terminalsAfter !== terminalsBefore) throw new Error("locked terminal shortcut opened a terminal");
    if (result.debugToolbarVisible || result.restrictedDebugViewVisible
        || result.chatVisible || result.taskQuickInputVisible) {
        throw new Error("locked debug/task shortcut opened restricted UI");
    }
    if (result.externalRequests.length || result.pageErrors.length) {
        throw new Error("browser observed external requests or page errors");
    }
    console.log(JSON.stringify(result));
})().catch(error => {
    console.error(error.stack || String(error));
    process.exit(1);
});
