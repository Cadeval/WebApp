import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

test('HTMX 4 request context preserves validation feedback, closes menus and keeps POST reading position', () => {
    const listeners = new Map();
    const content = {contains:node => node?.inside === true, querySelector:() => null};
    const menu = {contains:node => node?.menu === true, matches:() => true, hidePopover(){this.closed = true}};
    const body = {dataset:{},classList:{toggle(){}},addEventListener(type,callback){
        if (!listeners.has(type)) listeners.set(type,[]);
        listeners.get(type).push(callback);
    }};
    let scrollCount = 0;
    const doc = {body,addEventListener:body.addEventListener,querySelectorAll:() => [],querySelector:() => null,
        getElementById:id => ({'content-container':content,'menu-popover':menu})[id]};
    vm.runInNewContext(readFileSync(new URL('./htmx-override.js',import.meta.url),'utf8'),{window:{scrollTo(){scrollCount++}},document:doc,location:{pathname:'/',href:'http://localhost/'},URL});
    const emit = (name,event={}) => listeners.get(name)?.forEach(callback => callback(event));
    emit('htmx:before:request',{detail:{ctx:{target:content,sourceElement:{menu:true}}}});
    assert(menu.closed);
    emit('htmx:after:swap',{detail:{ctx:{request:{method:'GET'},target:{id:'content-container'}}}});
    emit('htmx:after:swap',{detail:{ctx:{request:{method:'POST'},target:{id:'content-container'}}}});
    assert.equal(scrollCount,1);
    for (const status of [200,400,409,422]) {
        let cancelled = false;
        emit('htmx:before:swap',{detail:{ctx:{response:{status}}},preventDefault(){cancelled=true}});
        assert.equal(cancelled,false);
    }
    for (const status of [403,404,500]) {
        let cancelled = false;
        emit('htmx:before:response',{detail:{ctx:{response:{status}}},preventDefault(){cancelled=true}});
        assert.equal(cancelled,true);
    }
});

test('the global plugin entry stays current across workspace tabs and nested model routes', () => {
    const listeners = new Map();
    const workspace = {dataset: {pluginWorkspaceUrl: '/plugins/bim/'}};
    const links = ['/plugins/manage/', '/plugins/bim/', '/plugins/ifc-editor/'].map(href => ({
        href, dataset: href === '/plugins/manage/' ? {} : {pluginWorkspaceUrl: href}, attributes: new Map(),
        setAttribute(key, value) { this.attributes.set(key, value); },
        removeAttribute(key) { this.attributes.delete(key); },
    }));
    const body = {dataset: {}, classList: {toggle() {}}, addEventListener(name, callback) {
        if (!listeners.has(name)) listeners.set(name, []);
        listeners.get(name).push(callback);
    }};
    let activeWorkspace = workspace;
    const doc = {body, addEventListener:body.addEventListener, getElementById: () => null,
        querySelectorAll: selector => selector === '#menu-popover a[href]' ? links : [],
        querySelector: selector => selector === '[data-plugin-workspace]' ? activeWorkspace : null};
    const location = {pathname: '/plugins/bim/models/42/view/', href: 'http://localhost/plugins/bim/models/42/view/?assessment=3'};
    vm.runInNewContext(readFileSync(new URL('./htmx-override.js', import.meta.url), 'utf8'),
        {window: {scrollTo() {}}, document: doc, location, URL});
    function settle() { for (const callback of listeners.get('htmx:after:settle')) callback({detail: {}}); }
    settle();
    assert.equal(links[1].attributes.get('aria-current'), 'page');
    assert.equal(links[0].attributes.has('aria-current'), false);
    assert.equal(links[2].attributes.has('aria-current'), false);
    location.pathname = '/plugins/bim/'; location.href = 'http://localhost/plugins/bim/?tab=compare'; settle();
    assert.equal(links[1].attributes.get('aria-current'), 'page');
    activeWorkspace = null; location.pathname = '/plugins/manage/'; location.href = 'http://localhost/plugins/manage/'; settle();
    assert.equal(links[0].attributes.get('aria-current'), 'page');
    assert.equal(links[1].attributes.has('aria-current'), false);
});

function navigationFixture() {
    const listeners = new Map(), focused = [], attributes = new Map();
    const heading = {textContent: 'Reference configurations', setAttribute(name, value) { attributes.set(name, value); }, focus(options) { focused.push({target: this, options}); }};
    const summary = {focus(options) { focused.push({target: this, options}); }, setAttribute() {}};
    const main = {id: 'main-content', setAttribute() {}, focus(options) { focused.push({target: this, options}); }};
    let errors = null, pageHeading = heading, headings = [heading], viewer = null;
    const content = {id: 'content-container', contains(node) { return node?.inside === true; },
        querySelectorAll(selector) { return selector === 'h1' ? headings : []; },
        querySelector(selector) { return selector === '[data-form-errors]' ? errors : selector === 'h1' ? pageHeading : null; }};
    const body = {dataset: {}, classList: {toggle() {}}};
    const doc = {body, title: 'Old title', addEventListener(name, callback) {
        if (!listeners.has(name)) listeners.set(name, []);
        listeners.get(name).push(callback);
    }, getElementById(id) { return {'content-container': content, 'main-content': main}[id]; },
    querySelectorAll() { return []; }, querySelector(selector) {
        if (selector === '#content-container h1') return pageHeading;
        if (selector === '[data-form-errors]') return errors;
        if (selector === '#viewer-app') return viewer;
        return null;
    }};
    let scrollCount = 0;
    vm.runInNewContext(readFileSync(new URL('./htmx-override.js', import.meta.url), 'utf8'),
        {document: doc, window: {scrollTo() { scrollCount++; }, getComputedStyle(element) { return {visibility: element.visibility || 'visible'}; }}, location: {pathname: '/', href: 'http://localhost/'}, URL});
    return {doc, content, main, heading, summary, focused, attributes, get scrollCount() { return scrollCount; },
        emit(name, detail = {}) { listeners.get(name)?.forEach(callback => callback({detail})); },
        setErrors(value) { errors = value; }, setViewer(value) { viewer = value; pageHeading = null; headings = []; },
        setHeadings(value) { headings = value; pageHeading = value[0] || null; }};
}

test('GET page navigation and main history restoration update the title and focus; POST and local refresh keep position', () => {
    const page = navigationFixture();
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.content}});
    assert.equal(page.doc.title, 'Reference configurations · Cadevil');
    assert.equal(page.attributes.get('tabindex'), '-1');
    assert.equal(page.focused[0].target, page.heading);
    assert.equal(page.focused[0].options.preventScroll, true);
    assert.equal(page.scrollCount, 1);
    page.emit('htmx:after:swap', {ctx: {request: {method: 'POST'}, target: page.content}});
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: {id: 'local-results'}}});
    assert.equal(page.focused.length, 1);
    assert.equal(page.scrollCount, 1);
    page.setViewer({dataset: {pageTitle: 'Office model'}});
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.main}});
    assert.equal(page.doc.title, 'Office model · Cadevil');
    assert.equal(page.focused[1].target, page.main);
    assert.equal(page.scrollCount, 2);
});

test('new validation summaries receive focus while an unrelated settled panel does not steal it', () => {
    const page = navigationFixture();
    page.setErrors(page.summary);
    page.emit('htmx:after:settle', {task: {target: page.content}});
    assert.equal(page.focused[0].target, page.summary);
    page.emit('htmx:after:settle', {task: {target: {querySelector() { return null; }}}});
    assert.equal(page.focused.length, 1);
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.content}});
    assert.equal(page.focused[1].target, page.summary);
});

test('shared page focus respects the workspace tab focus already restored during settle', () => {
    const page = navigationFixture();
    const tab = {inside: true, closest(selector) { return selector === '[data-workspace-tab]' ? this : null; }};
    page.doc.activeElement = tab;
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.content}});
    assert.equal(page.doc.title, 'Reference configurations · Cadevil');
    assert.equal(page.focused.length, 0);
    assert.equal(page.scrollCount, 0);
    page.setErrors(page.summary);
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.content}});
    assert.equal(page.focused[0].target, page.summary);
});

test('moving to a persistent control while a page request loads preserves the newer focus', () => {
    const page = navigationFixture();
    const source = {inside: true}, persistentControl = {};
    page.doc.activeElement = source;
    const ctx = {request: {method: 'GET'}, target: page.content};
    page.emit('htmx:before:request', {ctx});
    page.doc.activeElement = persistentControl;
    page.emit('htmx:after:swap', {ctx});
    assert.equal(page.doc.title, 'Reference configurations · Cadevil');
    assert.equal(page.focused.length, 0);
    assert.equal(page.scrollCount, 0);
});

test('a viewer heading hidden by its responsive parent falls back to the main landmark', () => {
    const page = navigationFixture();
    page.heading.getClientRects = () => [];
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.content}});
    assert.equal(page.doc.title, 'Reference configurations · Cadevil');
    assert.equal(page.focused.length, 1);
    assert.equal(page.focused[0].target, page.main);
    assert.equal(page.focused[0].options.preventScroll, true);
});

test('page focus skips hidden, inert and ARIA-hidden headings and chooses the first available heading', () => {
    const page = navigationFixture();
    const hidden = {textContent: 'Hidden heading', visibility: 'hidden'}, inert = {closest() { return {}; }}, ariaHidden = {closest() { return {}; }};
    page.setHeadings([hidden, inert, ariaHidden, page.heading]);
    page.emit('htmx:after:swap', {ctx: {request: {method: 'GET'}, target: page.content}});
    assert.equal(page.focused.length, 1);
    assert.equal(page.focused[0].target, page.heading);
});

test('hidden validation summaries do not receive focus or override preserved persistent-control focus', () => {
    const page = navigationFixture();
    page.summary.getClientRects = () => [];
    page.setErrors(page.summary);
    page.emit('htmx:after:settle', {task: {target: page.content}});
    assert.equal(page.focused.length, 0);
    const ctx = {request: {method: 'GET'}, target: page.content};
    page.doc.activeElement = {inside: true};
    page.emit('htmx:before:request', {ctx});
    page.doc.activeElement = {};
    page.emit('htmx:after:swap', {ctx});
    assert.equal(page.focused.length, 0);
});
