(function(global) {
    'use strict';

    const SAFE_MATH = /(\$\$[\s\S]+?\$\$|\$[^\n$]+?\$)/g;
    const PARAGRAPH_BREAK = /\n(?:[^\S\n]*\n)+/;

    function normaliseNewlines(value) {
        return String(value).replace(/\r\n?/g, '\n');
    }

    function layoutText(value) {
        const parts = normaliseNewlines(value).split(SAFE_MATH);
        for (let index = 1; index < parts.length; index += 2) {
            const math = parts[index].replace(/\n/g, ' ');
            if (math.startsWith('$$')) {
                parts[index - 1] = parts[index - 1].replace(/[ \t\n]+$/, '') + '\n\n';
                parts[index + 1] = '\n\n' + parts[index + 1].replace(/^[ \t\n]+/, '');
            }
            parts[index] = math;
        }
        return parts.join('')
            .replace(/^(?:[^\S\n]*\n)+/, '')
            .replace(/(?:\n[^\S\n]*)+$/, '');
    }

    function singleLine(value) {
        return normaliseNewlines(value)
            .replace(/[\t ]*\n[\t ]*/g, ' ')
            .trim();
    }

    function paragraphs(value) {
        const items = layoutText(value).split(PARAGRAPH_BREAK);
        return items.map(function(paragraph) {
            return paragraph.split('\n');
        });
    }

    function render(container, value) {
        const flow = document.createElement('div');
        flow.className = 'safe-text-flow';
        paragraphs(value).forEach(function(lines) {
            const paragraph = document.createElement('p');
            paragraph.className = 'safe-text-paragraph';
            lines.forEach(function(value) {
                const line = document.createElement('span');
                line.className = 'safe-text-line';
                line.textContent = value;
                paragraph.appendChild(line);
            });
            flow.appendChild(paragraph);
        });
        container.replaceChildren(flow);
    }

    global.DidacticCardsSafeText = Object.freeze({
        normaliseNewlines: normaliseNewlines,
        singleLine: singleLine,
        layoutText: layoutText,
        paragraphs: paragraphs,
        render: render,
    });
})(window);
