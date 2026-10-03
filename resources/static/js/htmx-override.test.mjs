import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

test('HTMX 4 request context preserves validation feedback and settles loading state', () => {
    const listeners = new Map();
    const classes = new Set();
    const content = {contains:node => node?.inside === true};
    const menu = {contains:node => node?.menu === true, matches:() => true, hidePopover(){this.closed = true}};
    const throbber = {classList:{add:value => classes.add(value),remove:value => classes.delete(value)}};
    const body = {dataset:{},classList:{toggle(){}},addEventListener(type,callback){
        if (!listeners.has(type)) listeners.set(type,[]);
        listeners.get(type).push(callback);
    }};
    const doc = {body,addEventListener(){},querySelectorAll:() => [],querySelector:() => null,
        getElementById:id => ({'content-container':content,'menu-popover':menu,'htmx-throbber':throbber})[id]};
    vm.runInNewContext(readFileSync(new URL('./htmx-override.js',import.meta.url),'utf8'),{document:doc,location:{pathname:'/',href:'http://localhost/'},URL});
    const emit = (name,event={}) => listeners.get(name)?.forEach(callback => callback(event));
    emit('htmx:before:request',{detail:{ctx:{target:content,sourceElement:{menu:true}}}});
    assert(classes.has('active')); assert(menu.closed);
    emit('htmx:finally:request',{detail:{ctx:{}}}); assert(!classes.has('active'));
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
