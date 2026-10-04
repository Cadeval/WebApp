// IFC declarations and saved calculation values stay visibly distinct.
export function formatPropertyValue(value, unit = '') {
    if (value == null || (typeof value === 'number' && !Number.isFinite(value))) return 'Unavailable';
    let formatted;
    if (typeof value === 'number') {
        formatted = new Intl.NumberFormat(undefined, unit === 'EUR' ? { minimumFractionDigits: 2, maximumFractionDigits: 2 } : Math.abs(value) > 0 && Math.abs(value) < 0.0001
            ? { notation: 'scientific', maximumSignificantDigits: 4 }
            : { maximumFractionDigits: 5 }).format(value);
    } else if (typeof value === 'boolean') formatted = value ? 'Yes' : 'No';
    else if (Array.isArray(value)) formatted = value.map(item => formatPropertyValue(item)).join(', ');
    else if (typeof value === 'object') formatted = JSON.stringify(value);
    else formatted = String(value).trim() || 'Unavailable';
    if (formatted === 'Unavailable') return formatted;
    if (unit === '1–5') return `${formatted} / 5`;
    const readableUnit = ({ EUR: '€', 'kg·m3^-1': 'kg/m³', 'JOULE·kg^-1·KELVIN^-1': 'J/(kg·K)' })[unit] || unit;
    return `${formatted}${readableUnit ? ` ${readableUnit}` : ''}`;
}

export function propertyGroups(properties = []) {
    const groups = new Map();
    for (const property of properties) {
        const name = property.set || property.source || 'Properties';
        if (!groups.has(name)) groups.set(name, []);
        groups.get(name).push(property);
    }
    return groups;
}

function text(documentRoot, tag, value, className) {
    const node = documentRoot.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
}

export function propertyList(documentRoot, properties) {
    const list = documentRoot.createElement('dl');
    list.className = 'part-metrics';
    for (const property of properties || []) {
        const row = documentRoot.createElement('div');
        row.append(text(documentRoot, 'dt', property.label), text(documentRoot, 'dd', formatPropertyValue(property.value, property.unit)));
        list.append(row);
    }
    return list;
}

function issues(documentRoot, assessment, parent) {
    if (!assessment) return;
    parent.append(text(documentRoot, 'p', assessment.status !== 'complete'
        ? 'Incomplete assessment · missing values are unavailable.'
        : 'Saved assessment · element material component', 'viewer-data-note'));
    const unique = new Map();
    for (const issue of assessment.issues || []) {
        const message = String(issue);
        unique.set(message, (unique.get(message) || 0) + 1);
    }
    if (unique.size) {
        const details = documentRoot.createElement('details');
        details.append(text(documentRoot, 'summary', 'Calculation issues'));
        const list = documentRoot.createElement('ul');
        for (const [message, count] of unique) list.append(text(documentRoot, 'li', `${message}${count > 1 ? ` · ${count} occurrences` : ''}`));
        details.append(list);
        parent.append(details);
    }
}

export function renderMaterialInspector(container, record, state = {}, documentRoot = globalThis.document) {
    container.replaceChildren();
    if (!record) {
        container.append(text(documentRoot, 'p', state.loading ? 'Loading IFC material properties…'
            : state.error || 'No material properties are available for this IFC element.', 'viewer-data-note'));
        return;
    }
    container.append(text(documentRoot, 'h3', 'Materials'));
    if (!record.materials?.length) container.append(text(documentRoot, 'p', 'No material is declared for this element.', 'viewer-data-note'));
    for (const material of record.materials || []) {
        const card = documentRoot.createElement('section');
        card.className = 'viewer-material';
        const ownAssessment = material.assessment && material.assessment.status !== 'shared';
        card.append(text(documentRoot, 'h4', material.name));
        card.append(text(documentRoot, 'p', material.association === 'assessment' ? 'Material recorded in the saved assessment'
            : `IFC material${material.material_id ? ` #${material.material_id}` : ''} · ${material.association || 'association'}`, 'viewer-data-note'));
        if (material.properties?.length) {
            const declared = documentRoot.createElement('details');
            declared.open = !ownAssessment;
            declared.append(text(documentRoot, 'summary', 'Declared material properties'), propertyList(documentRoot, material.properties));
            card.append(declared);
        }
        if (material.assessment?.status === 'shared') {
            card.append(text(documentRoot, 'p', material.assessment.notice || 'Combined assessment values for this material name are listed separately.', 'viewer-data-note'));
        } else if (ownAssessment) {
            const primary = new Set(['dichte', 'mass', 'gwp_a1_a3', 'global_brutto_price', 'recycling_grade']);
            card.append(text(documentRoot, 'p', material.assessment.scope === 'element material name group'
                ? 'Saved assessment · combined values for this material name' : material.assessment.status === 'complete'
                ? 'Saved assessment · material reference and component values'
                : 'Incomplete component assessment · missing values are unavailable', 'viewer-data-note'));
            card.append(propertyList(documentRoot, (material.assessment.properties || []).filter(property => primary.has(property.key))));
            const details = documentRoot.createElement('details');
            details.append(text(documentRoot, 'summary', 'Material properties & calculations'));
            issues(documentRoot, material.assessment, details);
            details.append(propertyList(documentRoot, material.assessment.properties));
            card.append(details);
        } else if (!material.properties?.length) {
            card.append(text(documentRoot, 'p', 'Physical and calculation properties are unavailable. Select a matching material assessment to add them.', 'viewer-data-note'));
        }
        container.append(card);
    }
    if (record.assessment) {
        const details = documentRoot.createElement('details');
        details.append(text(documentRoot, 'summary', 'Element totals · all its materials'));
        details.append(text(documentRoot, 'p', record.assessment.status === 'partial' ? 'Incomplete totals · unavailable values are not zero.' : 'Saved assessment · element total', 'viewer-data-note'));
        details.append(propertyList(documentRoot, record.assessment.properties));
        container.append(details);
    }
    if (record.properties?.length) {
        const details = documentRoot.createElement('details');
        details.className = 'viewer-ifc-properties';
        details.append(text(documentRoot, 'summary', 'Declared IFC properties'));
        for (const [name, properties] of propertyGroups(record.properties)) {
            const group = documentRoot.createElement('details');
            group.append(text(documentRoot, 'summary', name), propertyList(documentRoot, properties));
            details.append(group);
        }
        container.append(details);
    }
}
