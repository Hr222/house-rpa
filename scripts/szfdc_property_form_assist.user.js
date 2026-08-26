// ==UserScript==
// @name         深圳市房地产信息平台 - 产权查询表单辅助
// @namespace    jeethink-rpa.local
// @version      1.0.0
// @description  仅辅助预填产权查询条件；不自动查询、不读取或导出产权结果。
// @match        https://fdc.zjj.sz.gov.cn/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(() => {
    'use strict';

    const PANEL_ID = 'szfdc-property-form-assist';
    const STATUS_ID = `${PANEL_ID}-status`;
    const FIELD_LABELS = {
        certificateNo: '产权证书编号',
        identityNo: '权利人或法人代表身份证号',
        organizationName: '产权证上的机构名称',
    };

    function normalizeText(value) {
        return (value || '').replace(/\s+/g, '').trim();
    }

    function isTextInput(element) {
        if (!(element instanceof HTMLInputElement) || element.disabled || element.readOnly) {
            return false;
        }

        return ['text', 'search', 'tel', 'number', ''].includes(element.type);
    }

    function visibleInputs() {
        return [...document.querySelectorAll('input')].filter((input) => {
            if (input.closest(`#${PANEL_ID}`) || !isTextInput(input)) {
                return false;
            }

            const rect = input.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        });
    }

    function findInputForLabel(labelText) {
        const targetText = normalizeText(labelText);
        const allInputs = visibleInputs();
        const labels = [...document.querySelectorAll('label, span, div, td, th, p')].filter((element) => {
            if (element.closest(`#${PANEL_ID}`)) {
                return false;
            }
            const text = normalizeText(element.textContent);
            return text.includes(targetText) && text.length <= targetText.length + 12;
        });

        let bestMatch = null;
        for (const label of labels) {
            const labelRect = label.getBoundingClientRect();
            for (const input of allInputs) {
                const inputRect = input.getBoundingClientRect();
                const verticalOffset = Math.abs(
                    (inputRect.top + inputRect.height / 2) - (labelRect.top + labelRect.height / 2),
                );
                const horizontalOffset = inputRect.left - labelRect.right;

                if (verticalOffset > 48 || horizontalOffset < -16 || horizontalOffset > 520) {
                    continue;
                }

                const score = verticalOffset * 10 + Math.max(horizontalOffset, 0);
                if (!bestMatch || score < bestMatch.score) {
                    bestMatch = { input, score };
                }
            }
        }

        return bestMatch?.input ?? null;
    }

    function setInputValue(input, value) {
        const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        descriptor?.set?.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function setStatus(message, isError = false) {
        const status = document.getElementById(STATUS_ID);
        if (!status) {
            return;
        }

        status.textContent = message;
        status.style.color = isError ? '#b42318' : '#175cd3';
    }

    function getPanelValue(name) {
        const input = document.querySelector(`#${PANEL_ID} [name="${name}"]`);
        return input?.value.trim() || '';
    }

    function fillForm() {
        const certificateNo = getPanelValue('certificateNo');
        const identityNo = getPanelValue('identityNo');
        const organizationName = getPanelValue('organizationName');

        if (!certificateNo) {
            setStatus('请填写产权证书编号。', true);
            return;
        }
        if (!identityNo && !organizationName) {
            setStatus('请填写身份证号或机构名称其中一项。', true);
            return;
        }

        const values = { certificateNo, identityNo, organizationName };
        const missing = [];
        for (const [name, label] of Object.entries(FIELD_LABELS)) {
            if (!values[name]) {
                continue;
            }

            const input = findInputForLabel(label);
            if (!input) {
                missing.push(label);
                continue;
            }

            setInputValue(input, values[name]);
        }

        if (missing.length) {
            setStatus(`未定位到页面字段：${missing.join('、')}。请勿提交查询。`, true);
            return;
        }

        setStatus('条件已填入页面。请人工核对后，使用网站原有“查询”按钮提交。');
    }

    function clearPanel() {
        document.querySelectorAll(`#${PANEL_ID} input`).forEach((input) => {
            input.value = '';
        });
        setStatus('已清空脚本面板，页面已有内容未改动。');
    }

    function mountPanel() {
        if (document.getElementById(PANEL_ID) || !document.body) {
            return;
        }

        const panel = document.createElement('section');
        panel.id = PANEL_ID;
        panel.innerHTML = `
            <div class="szfdc-assist-title">产权查询表单辅助</div>
            <label>产权证书编号<input name="certificateNo" autocomplete="off" inputmode="numeric"></label>
            <label>身份证号<input name="identityNo" autocomplete="off"></label>
            <label>机构名称<input name="organizationName" autocomplete="off"></label>
            <div class="szfdc-assist-actions">
                <button type="button" data-action="fill">填入页面</button>
                <button type="button" class="secondary" data-action="clear">清空</button>
            </div>
            <p id="${STATUS_ID}">仅预填条件；查询、弹窗确认和结果查看均由操作员完成。</p>
        `;
        document.body.append(panel);

        panel.querySelector('[data-action="fill"]')?.addEventListener('click', fillForm);
        panel.querySelector('[data-action="clear"]')?.addEventListener('click', clearPanel);
    }

    function addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            #${PANEL_ID} {
                position: fixed;
                right: 20px;
                bottom: 20px;
                z-index: 2147483647;
                width: 280px;
                padding: 14px;
                background: #ffffff;
                border: 1px solid #98a2b3;
                border-radius: 6px;
                box-shadow: 0 8px 24px rgba(16, 24, 40, 0.18);
                color: #1d2939;
                font: 13px/1.5 Arial, "Microsoft YaHei", sans-serif;
            }
            #${PANEL_ID} .szfdc-assist-title {
                margin-bottom: 10px;
                font-weight: 700;
            }
            #${PANEL_ID} label {
                display: block;
                margin: 8px 0;
            }
            #${PANEL_ID} input {
                box-sizing: border-box;
                width: 100%;
                height: 30px;
                margin-top: 3px;
                padding: 4px 7px;
                border: 1px solid #98a2b3;
                border-radius: 4px;
                color: #1d2939;
            }
            #${PANEL_ID} .szfdc-assist-actions {
                display: flex;
                gap: 8px;
                margin-top: 12px;
            }
            #${PANEL_ID} button {
                min-width: 72px;
                height: 30px;
                border: 1px solid #175cd3;
                border-radius: 4px;
                background: #175cd3;
                color: #ffffff;
                cursor: pointer;
            }
            #${PANEL_ID} button.secondary {
                border-color: #98a2b3;
                background: #ffffff;
                color: #344054;
            }
            #${PANEL_ID} p {
                margin: 10px 0 0;
                color: #475467;
                font-size: 12px;
            }
        `;
        document.head.append(style);
    }

    addStyles();
    mountPanel();
    new MutationObserver(mountPanel).observe(document.documentElement, { childList: true, subtree: true });
})();
