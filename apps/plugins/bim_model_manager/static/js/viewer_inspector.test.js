import assert from 'node:assert/strict';
import test from 'node:test';
import { formatPropertyValue, propertyGroups, renderMaterialInspector } from './viewer_inspector.js';

class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
}
const documentRoot = { createElement: tag => new Node(tag) };
const flatten = node => [node, ...node.children.flatMap(flatten)];

test('physical and Euro values retain units, small coefficients, zero and unavailable distinctions', () => {
    assert.equal(formatPropertyValue(12.34567, 'EUR'), '12.35 €');
    assert.equal(formatPropertyValue(0, 'kg'), '0 kg');
    assert.equal(formatPropertyValue(null, 'EUR'), 'Unavailable');
    assert.equal(formatPropertyValue(Infinity, 'MJ'), 'Unavailable');
    assert.notEqual(formatPropertyValue(0.00000123, 'kg SO₂e/kg'), '0 kg SO₂e/kg');
    assert.equal(formatPropertyValue(false), 'No');
});

test('declared property sets keep their grouping and source', () => {
    const density = { label: 'Density', value: 2400, set: 'Pset_MaterialCommon', source: 'IFC' };
    const cost = { label: 'Cost', value: 20, source: 'Saved assessment' };
    assert.deepEqual([...propertyGroups([density, cost])], [['Pset_MaterialCommon', [density]], ['Saved assessment', [cost]]]);
});

test('selected component values stay separate from totals and IFC text never becomes markup', () => {
    const container = new Node('div');
    renderMaterialInspector(container, {
        materials: [{ name: '<img src=x onerror=alert(1)>', association: 'layer', properties: [{ label: 'Layer thickness', value: 0.2, unit: 'm' }],
            assessment: { status: 'partial', properties: [{ label: 'GWP A1–A3', value: 11, unit: 'kg CO₂e' }], issues: ['Missing price', 'Missing price'] } }],
        assessment: { status: 'partial', properties: [{ label: 'GWP A1–A3', value: null, unit: 'kg CO₂e' }] },
        properties: [{ label: 'IsExternal', value: true, set: 'Pset_WallCommon' }],
    }, {}, documentRoot);
    const nodes = flatten(container), words = nodes.map(node => node.textContent);
    assert.ok(words.includes('<img src=x onerror=alert(1)>'));
    assert.ok(words.includes('11 kg CO₂e') && words.includes('Unavailable'));
    assert.ok(words.includes('Element totals · all its materials'));
    assert.ok(words.includes('Missing price · 2 occurrences'));
    assert.equal(nodes.filter(node => node.tag === 'img').length, 0);
});

test('missing material declarations and failed metadata remain readable and nonfatal', () => {
    const container = new Node('div');
    renderMaterialInspector(container, { materials: [] }, {}, documentRoot);
    assert.ok(flatten(container).some(node => node.textContent === 'No material is declared for this element.'));
    renderMaterialInspector(container, null, { error: 'Properties unreadable; model available.' }, documentRoot);
    assert.equal(container.children.length, 1);
    assert.equal(container.children[0].textContent, 'Properties unreadable; model available.');
});

test('same-name declarations retain their identities and combined assessment values appear once', () => {
    const container = new Node('div');
    renderMaterialInspector(container, { materials: [
        ...[2400, 1200].map((density, index) => ({ name: 'Concrete', material_id: index + 100, association: 'layer',
            properties: [{ label: 'Mass density', value: density, unit: 'kg/m³' }],
            assessment: { status: 'shared', notice: 'Combined values are listed separately.', properties: [] } })),
        { name: 'Concrete', association: 'assessment', assessment: { scope: 'element material name group', status: 'complete',
            properties: [{ key: 'mass', label: 'Installed mass', value: 1000, unit: 'kg' }] } },
    ] }, {}, documentRoot);
    const words = flatten(container).map(node => node.textContent);
    assert.ok(words.includes('IFC material #100 · layer') && words.includes('IFC material #101 · layer'));
    assert.ok(words.includes('2,400 kg/m³') && words.includes('1,200 kg/m³'));
    assert.ok(words.includes('Saved assessment · combined values for this material name'));
    assert.equal(words.filter(value => value === '1,000 kg').length, 2); // summary and expanded breakdown, never per declaration
});
