import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

test('HTMX 4 request context preserves validation feedback, closes menus and keeps POST reading position', () => {
    const listeners = new Map();
    const content = {contains:node => node?.inside === true};
    const menu = {contains:node => node?.menu === true, matches:() => true, hidePopover(){this.closed = true}};
    const body = {dataset:{},classList:{toggle(){}},addEventListener(type,callback){
        if (!listeners.has(type)) listeners.set(type,[]);
        listeners.get(type).push(callback);
    }};
    let scrollCount = 0;
    const doc = {body,addEventListener(){},querySelectorAll:() => [],querySelector:() => null,
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
    const doc = {body, addEventListener() {}, getElementById: () => null,
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
