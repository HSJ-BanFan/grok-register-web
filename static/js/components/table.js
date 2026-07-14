export function createTable(container, options) {
    const { columns, data, onRowSelect, emptyText = 'No data', selectable = true } = options;
    let selectedIds = new Set();

    function render() {
        container.innerHTML = '';

        if (!data || data.length === 0) {
            container.innerHTML = `<div class="table-empty" style="animation: fadeIn 0.4s ease-out;">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" style="margin-bottom:14px;opacity:0.4;">
                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                    <path d="M3 9h18"/>
                    <path d="M9 21V9"/>
                </svg>
                <div style="color:var(--text-muted);font-size:13px;line-height:1.5;">${emptyText}</div>
            </div>`;
            return;
        }

        const table = document.createElement('table');
        table.className = 'data-table';

        // Header
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        if (selectable) {
            const th = document.createElement('th');
            th.className = 'th-checkbox';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'select-all';
            checkbox.addEventListener('change', () => {
                const checkboxes = table.querySelectorAll('.row-checkbox');
                checkboxes.forEach(cb => {
                    cb.checked = checkbox.checked;
                    const id = parseInt(cb.dataset.id);
                    if (checkbox.checked) selectedIds.add(id);
                    else selectedIds.delete(id);
                });
                if (onRowSelect) onRowSelect([...selectedIds]);
            });
            th.appendChild(checkbox);
            headerRow.appendChild(th);
        }
        columns.forEach(col => {
            const th = document.createElement('th');
            th.textContent = col.title;
            if (col.width) th.style.width = col.width;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        // Body
        const tbody = document.createElement('tbody');
        data.forEach((row, idx) => {
            const tr = document.createElement('tr');
            if (selectable && row.id !== undefined) {
                const td = document.createElement('td');
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'row-checkbox';
                checkbox.dataset.id = row.id;
                checkbox.checked = selectedIds.has(row.id);
                checkbox.addEventListener('change', () => {
                    const id = parseInt(checkbox.dataset.id);
                    if (checkbox.checked) selectedIds.add(id);
                    else selectedIds.delete(id);
                    if (onRowSelect) onRowSelect([...selectedIds]);
                });
                td.appendChild(checkbox);
                tr.appendChild(td);
            }
            columns.forEach(col => {
                const td = document.createElement('td');
                if (col.render) {
                    const content = col.render(row, idx);
                    if (typeof content === 'string') td.innerHTML = content;
                    else td.appendChild(content);
                } else {
                    td.textContent = row[col.key] ?? '';
                }
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        container.appendChild(table);
    }

    render();

    return {
        getSelectedIds: () => [...selectedIds],
        clearSelection: () => { selectedIds.clear(); render(); },
        refresh: () => render(),
    };
}
